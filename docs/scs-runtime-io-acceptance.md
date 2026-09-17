# Phase 1 Executor Closed-Loop Acceptance Report — SCS Runtime I/O Rework

Date: 2026-08-30. Device: StackChan CoreS3, ESP32-S3 rev 0.2, MAC `<device-mac>`,
16 MiB flash, USB-Serial/JTAG, port `/dev/cu.usbmodemXXXX`.
Firmware under test: `lifeos-phase1-0.3.0` (HIL profile, app image
SHA-256 `99bd2e7463d51af7ae58b520487f2066f975e8850072d31159888ab410133377`).

Verdict: **Phase 1 执行器闭环子门禁 PASS**（real-motion closed loop）。这不是完整
Phase 1 出口：端到端急停 `<100 ms` 尚未满足，触摸、8 小时 soak、真实机械卡滞及
若干独立时序/传感器读取项仍为 BLOCKED/NOT TESTED。

## Root cause that was fixed

The 20 Hz safety task owned blocking SCS UART transactions
(`read_position`/`set_position`/`stop`), and `set_position` silently re-ran the
full boot self-test (≥1 s). While blocked, it starved the USB main task on core
0, delayed `maintenance_motion` ACKs past the host timeout, and made stall
timing/TTL/feedback sampling interfere. The idle path also re-issued
`stop(true)` every 20 ms, keeping the servo bus permanently busy. See
`docs/scs-runtime-io-design.md` for the target architecture (ServoIoTask as the
single UART owner, non-blocking 20 Hz safety task, seqlock command mailbox and
timestamped feedback snapshot, generation-based cancellation, emergency VM_EN
fast cut).

## Changed files (this task)

- `docs/scs-runtime-io-design.md` — design document (new)
- `firmware/include/lifeos/runtime/servo_io.hpp`, `firmware/src/runtime/servo_io.cpp` —
  host-testable ServoIoCore state machine, StallMonitor, ServoLink, safety_gate (new)
- `firmware/idf/main/app_main.cpp` — task repartition (safety 0/20 @20 ms
  `vTaskDelayUntil`, servo_io 1/12, graph 1/5, main 0/1), emergency VM cut,
  console discipline, status fields
- `firmware/include/lifeos/hal/stackchan/stackchan.hpp`,
  `firmware/src/hal/stackchan/stackchan.cpp` — Py32 mutex + `cut_power_fast()`,
  servo-pair primitives (`wake_light`, `read_feedback_pair`, `enable_torque_pair`,
  `write_goal_pair`, `torque_off_pair`), removed implicit `power_on()` from
  `set_position`
- `firmware/tests/runtime/test_servo_io.cpp` — 18 host regressions (new)
- `firmware/CMakeLists.txt`, `firmware/idf/components/lifeos_core/CMakeLists.txt`,
  `tools/run_firmware_tests.sh` — build wiring
- `tools/phase1_hil.py` — staged evidence runner (trajectory sampling,
  freshness-gated settling, transient injection, soak, raw log tee)
- `tools/capture_port.py` — boot log capture (new)

## Tests run

- Host: `make test` (per brief env) — brain 5 passed; firmware suites
  (legacy/runtime/behavior/hal/protocol/phase1_controller/**servo_io**) passed;
  phase1 replay 10/10 OK; schema + codex contract PASS.
- New `servo_io_tests`: 18 regressions covering all 17 required cases plus the
  post-wake grace behaviour.
- Target build: ESP-IDF 5.5.4 HIL profile, `Project build complete`
  (app 0x64bxx bytes, 61% partition headroom).

## Flash verification

9 app-partition (0x10000) writes, each gated by identity re-check (esptool
`chip_id`: ESP32-S3 rev 0.2 + MAC match) and each ending `Hash of data
verified.` No full erase, no eFuse/encryption/partition changes. Image SHA-256s
in order: `24bb00a5…`, `ac26691a…`, `1c944612…`, `fcebfb76…`, `31f5fe70…`,
`b9a3cc03…`, `fc49a2d8…`, `7108397e…`, final `99bd2e74…` (full hashes in the
shell logs; final image recorded above). Recovery backup untouched and
re-verified before every flash:
`<secure-backup-dir>/stackchan-device-20260829-fullflash.bin`
SHA-256 `669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35`.

## Real HIL evidence (artifacts/scs-runtime-io/)

- **A startup** (`phaseA-startup.log`): no checksum mismatch; self-test
  `yaw_ping=1 yaw_feedback=1 pitch_ping=1 pitch_feedback=1 torque_off=1
  raw=485/632`; `LIFEOS_HIL_READY … motion=enabled board=ready`;
  `servo_io power state=ready_torque_off`; torque off at boot.
- **B protocol**: hello.device stable; device-side ACK latency logged
  (`gateway command accepted … latency_ms`: status 15–20 ms, motion 6–7 ms).
- **C motion 12/52** (`hil-final.log`, run11/`hil-final.json`): ACK accepted;
  torque on by ~200 ms; yaw 1.56→10.31, pitch 45→51.25 (settled within 2°
  tolerance, SCS steady-state error documented); fresh timestamped feedback
  (`feedback ok generation=1 raw=493/640 angle=10.31/51.25`); no false STALL;
  torque released after TTL; fault=false, safety_faults=0 throughout.
- **D pause**: pause ACK; paused=true; torque=false in the paused status
  (device-side VM cut `safety power_cut reason=pause elapsed_ms=1`); io
  `power_off`; no further motion.
- **E home**: resume+home reaches 1.25–1.56°/45.0° with no STALL; torque
  released on expiry.
- **F fault matrix**: transient failures (`feedback_transient`) degrade without
  latching, motion completes; frozen feedback latches STALL (~0.5 s), torque
  off, VM off, `clear_fault` recovers; hard limit 91° latches without ever
  reaching the servo path; emergency stop cuts VM_EN in **1 ms device-side**
  (`safety power_cut reason=emergency elapsed_ms=1`), host-observed stop
  latency 202 ms = 2 USB-CDC round trips; home blocked while latched;
  link-loss cancels the generation and cuts power with no auto-resume after
  reconnect (`fault=false torque=false link_lost=false paused=false`).
- **G full runner**: `python3 tools/phase1_hil.py --port /dev/cu.usbmodemXXXX
  --allow-hardware --soak-seconds 30 --soak-interval 2` →
  `status: PASS` (`hil-final.json`, 30 s soak, 15 samples, heap/PSRAM flat).

## PASS / BLOCKED / NOT TESTED matrix

| Item | Result |
|---|---|
| Real maintenance_motion ACK + torque/position write | PASS |
| yaw/pitch real feedback moves toward target | PASS (12/52 and home) |
| No false STALL | PASS |
| Pause → torque off, VM off per policy | PASS |
| Home recovery | PASS |
| Transient feedback failures (real, injected) | PASS |
| Persistent feedback stale (injected freeze) | PASS |
| Hard limit latch | PASS |
| Emergency stop, VM_EN < 100 ms device-side | PASS (1 ms) |
| Emergency stop end-to-end < 100 ms | BLOCKED (host-observed 209.6 ms) |
| Link loss + no auto-resume | PASS |
| Link-loss 1500 ms-to-power-cut direct timing | NOT TESTED |
| clear_fault recovery + no old-command resume | PASS |
| Host tests + ESP-IDF HIL build | PASS |
| `--require-touch` supervised touch pause/clear | NOT TESTED (no human present) |
| Independent sensor read / GC0308 capture sample | NOT TESTED |
| 8-hour soak (`--soak-seconds 28800`) | NOT TESTED (needs explicit request) |
| Real mechanical stall (hand-blocking) | NOT TESTED by design; covered by HIL freeze/progress-stall injection + host tests |

## Residual risks / notes

- SCS steady-state position error ≈1.3–1.7° with the default 16/5 raw-mapping
  (servo deadband); settle tolerance in the runner is ±2°.
- Just-woken servos read intermittently for ~0.5–1 s; handled by the
  post-wake grace (1.2 s) plus the independent 700 ms no-progress monitor.
- USB-Serial/JTAG host teardown quirks: runner retries the hello phase once;
  device-side console discipline prevents the wedge from returning.
- Unrelated dirty files in the working tree (docs, brain, contracts) were
  present before and after this task and were left untouched.
