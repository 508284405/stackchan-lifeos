# SCS Runtime I/O Design — ServoIoTask / Non-Blocking Safety Task

Status: implemented for Phase 1 executor closed loop (yaw/pitch SCS servos on StackChan CoreS3).

Root cause addressed: the 20 Hz safety task owned the blocking SCS UART path
(`read_position` / `set_position` / `stop`), and `set_position` silently re-ran the
full boot self-test (`power_on()`, ≥1 s with VM settle and retries). While the
safety task executed those transactions it starved the USB main task on the same
core, delayed `maintenance_motion` ACKs past the host timeout, and made stall
timing, TTL handling and feedback sampling interfere with each other. The idle
path additionally re-issued `stop(true)` (2 UART writes + VM_EN off) every 20 ms,
keeping the servo bus permanently busy.

## 1. Task partition

| Task             | Core | Prio | Period / trigger        | Stack  | Duties |
|------------------|------|------|-------------------------|--------|--------|
| `lifeos_safety`  | 0    | 20   | 20 ms, `vTaskDelayUntil`| 8 KiB  | pure gate: reads command mailbox + feedback snapshot, evaluates emergency/pause/link-loss/hard-limit/freshness/stall, publishes `OutputRequest`, latches faults |
| `lifeos_servo_io`| 1    | 12   | loop (2 ms idle delay)  | 6 KiB  | single owner of all SCS UART transactions and VM_EN/torque power sequencing; publishes timestamped feedback snapshot |
| `lifeos_graph`   | 1    | 5    | 100 ms                  | 8 KiB  | touch/IMU/proximity/perception sampling, display render, heartbeat (unchanged, never touches servo) |
| main (USB)       | 0    | 1    | line-driven             | 64 KiB | protocol parse, admission checks, writes command mailbox, ACKs immediately, executes emergency VM cut |

Hard rules:

- Only `lifeos_servo_io` calls SCS UART operations (ping, torque, position
  write, feedback read) or the full/light power sequences. Safety, USB main and
  graph tasks never do.
- The 20 Hz safety tick performs no blocking calls: no UART, no I²C wait, no
  mutex with unbounded timeout. It only reads seqlock-protected snapshots and
  atomics, computes the gate decision, and publishes it.
- The safety tick uses `vTaskDelayUntil` so execution time never accumulates
  into the schedule.

## 2. Single UART ownership

`ServoLink` (interface in `lifeos/runtime/servo_io.hpp`) is the only door to the
hardware:

```cpp
class ServoLink {
  virtual bool vm_enabled() const;
  virtual bool set_vm_enabled(bool enabled);          // Py32/I2C, short timeout
  virtual bool enable_torque(bool enabled);           // SCS reg 40, yaw+pitch
  virtual bool write_goal(const ServoPosition&, std::uint16_t& yaw_raw, std::uint16_t& pitch_raw);
  virtual bool read_feedback(ServoPosition& out);     // SCS reg 56, yaw+pitch
  virtual bool wake_light();                          // VM on + settle + 2 reads + torque-off verify
  virtual bool power_on_full();                       // cold/fault-recovery self-test (≈0.5–1 s)
  virtual bool hardware_ready() const;
};
```

Target adapter `StackChanServoLink` (app_main.cpp) delegates to
`StackChanServoPair`/`Py32IoExpander` primitives. Host tests substitute a fake
link with scripted delays/failures. The emergency VM_EN cut is deliberately
*outside* the io task loop (section 7).

## 3. Command mailbox

Single-slot latest-wins mailbox (`ServoIoCore`), one writer = USB main task,
reader = safety task, protected by a 32-bit seqlock:

```cpp
struct ServoCommand {
  std::uint32_t generation;   // from a monotonic core counter
  float yaw_deg, pitch_deg;
  std::uint64_t expires_at_ms; // TTL; maintenance_motion = now+2500 ms, home = now+1000 ms
};
```

- `submit(yaw, pitch, expires)` validates finiteness, allocates the next
  generation, stores the command, returns the generation. It performs no
  hardware access and is O(nanoseconds), so the USB ACK path can never be
  delayed by servo I/O (host test 1).
- `cancel_up_to(generation)` is a CAS-max on an atomic; callable from main
  (pause/emergency handlers), safety (fault latches, link loss) and tests.
- A command is *active* iff `generation > cancel_gen && expires_at_ms > now`.
  Expired or cancelled commands are never published to the io task, so expiry
  never delays a newer command (host test 9).

## 4. Timestamped feedback snapshot

One writer = `lifeos_servo_io`, readers = safety task / status, seqlock-copied:

```cpp
struct ServoFeedbackSnapshot {
  std::uint64_t sampled_at_ms;          // last successful feedback read (monotonic)
  std::uint32_t command_generation;     // generation the io task is executing
  std::uint64_t command_applied_ms;     // 0 until BOTH position writes ACKed
  float yaw_deg, pitch_deg;
  bool valid;                           // at least one real feedback sample seen
  bool torque_enabled;
  bool vm_enabled;
  std::uint32_t consecutive_failures;   // failed feedback reads since last success
  ServoIoState io_state;
  ServoIoError last_error;
};
```

Guarantees (host tests 4, 14, 15):

- `sampled_at_ms` is only written from real SCS reads and never goes backwards.
- `command_applied_ms` is set only after a successful position write of that
  generation; a cancelled/superseded transaction never overwrites a newer
  generation's fields.
- Write success and feedback success are independent: `command_applied_ms` and
  `sampled_at_ms`/`valid` track different events.

## 5. Servo I/O state machine (`ServoIoTask`)

States: `PowerOff → PoweringOn → ReadyTorqueOff → ApplyingCommand → Tracking →
Stopping → {PowerOff | ReadyTorqueOff}`, plus terminal-until-reset `Faulted`.

- `PowerOff`: no UART traffic. Wake triggers: `OutputRequest::Motion` (light
  wake) or `full_test_next` (after fault recovery, full self-test).
- `PoweringOn`: light wake = VM_EN on + settle + one position read per servo +
  torque-off verify (≈250 ms). Full self-test = existing `power_on()` sequence.
  Every step re-checks cancellation/emergency between blocking calls.
- `ReadyTorqueOff`: VM on, torque off. Keeps feedback fresh with a 100 ms idle
  sample period. First accepted command → `ApplyingCommand`.
- `ApplyingCommand`: torque enable (if needed) → position write (yaw+pitch). On
  write success publishes `command_applied_ms` and enters `Tracking`. A new
  generation, stop, cancel or emergency aborts the sequence *before* the write
  and never latches the old generation as applied.
- `Tracking`: samples feedback every 30 ms while a command is active; a new
  generation switches straight to `ApplyingCommand` (torque already on).
- `Stopping`: torque disable (UART) first, then VM_EN off if a power cut was
  requested; otherwise returns to `ReadyTorqueOff` (supervised idle keeps VM on
  so the next command takes the fast path).
- `Faulted`: entered on torque/position-write failure or wake failure; torque
  off + VM off first, stays until `OutputRequest::Reset` (clear_fault), then
  `PowerOff` with `full_test_next` so recovery re-runs the complete self-test.

Cold boot keeps the existing synchronous self-test inside `board.begin()` (main
task, before tasks start) so the startup log and `motion=enabled` evidence are
unchanged; the io task starts in `ReadyTorqueOff` (VM on, torque off) or
`Faulted` if the boot self-test failed.

## 6. Safety state machine (20 Hz tick)

1. Copy shared flags (paused / emergency / fault / feedback_frozen / host link).
2. Copy mailbox command; drop it if cancelled or expired (`take_command`).
3. Copy feedback snapshot; compute `FeedbackHealth`:
   `fresh = valid && now - sampled_at_ms ≤ kFeedbackFreshnessMs`;
   `stale_latch = consecutive_failures ≥ kFeedbackMinFailuresForLatch && !fresh`;
   1–2 failures or still-fresh = `degraded` only (logged, never latched).
4. Stall monitor (generation-keyed): monitoring starts only when the snapshot
   shows `command_applied_ms != 0` for the active generation; progress refresh
   requires ≥ `kStallProgressDeltaDeg` (0.5°) of yaw or pitch movement;
   stall latches when target error ≥ `kStallTargetErrorDeg` (3°) for
   ≥ `kStallNoProgressWindowMs` (700 ms).
5. `FastSafetyLoop::tick(sample, request)` — unchanged latch semantics for
   EmergencyStop / Stall / HardLimit / Watchdog; request hard limits (±90° yaw,
   5–85° pitch) are checked here, so a hard-limit command is never published to
   the io task (host test 16).
6. Publish exactly one `OutputRequest`: `Motion{generation, yaw, pitch}` when
   the decision is accepted, `Stop{power_cut}` otherwise (power cut when a
   critical fault is latched, or pause / emergency / link loss), `Reset` after a
   locally confirmed clear_fault.
7. Publish consolidated status atomics (position from snapshot,
   `torque_enabled = torque_atomic && vm_atomic`, io state, faults).

## 7. Emergency stop and VM_EN cut

Order (device-side budget < 100 ms, measured by the HIL runner):

1. Main task `command.emergency_stop` handler atomically bumps
   `emergency_generation` (doubles as cancel-all) and latches `emergency` +
   `fault` flags — no UART involved.
2. VM_EN is cut *first and directly* via `Py32IoExpander::cut_power_fast()`
   (read-modify-write of the output register only, 2 short I²C transactions,
   mutex-guarded with a bounded wait). It does not wait for any SCS transaction
   or torque-disable ACK.
3. Desired command cleared (`cancel_up_to` + mailbox superseded), emergency
   latched, ACK returned.
4. `lifeos_servo_io` observes the emergency generation on its next loop,
   drops any in-flight command, records VM/torque cache state and publishes the
   snapshot (`io_state=PowerOff`, `last_error=EmergencyStop`).

`Py32IoExpander` public mutations are guarded by a FreeRTOS mutex so the
emergency cut, the io task and boot code cannot interleave I²C
read-modify-writes. The shared I²C bus itself is serialized by the ESP-IDF
`i2c_master` driver bus lock; graph task sensors and the Py32 device coexist on
it without extra locking. The emergency path uses a bounded mutex wait and a
retry; if both attempts fail it logs and still latches the fault, and the io
task performs a backup torque-off/VM-off in its own context.

## 8. Command generation and cancellation

- One monotonic `uint32_t` generation counter (wraparound-safe, ≥49 days at
  1 kHz).
- Cancellation sources: pause, emergency stop, link loss, latched critical
  fault, clear_fault (recovery), command expiry (time-based).
- A cancelled generation is never executed late: the io task re-checks
  `cancel_gen` before each blocking step and between torque-enable and the
  position write; late completions of an abandoned sequence cannot set
  `command_applied_ms` for that generation (host tests 9, 10, 11, 14).
- `clear_fault` never resurrects an old command: clearing bumps
  `cancel_up_to(current)` and the mailbox entry stays cancelled (host test 13).

## 9. Feedback freshness and consecutive-failure policy

Named constants (in `servo_io.hpp`, all under host test):

| Constant | Value | Meaning |
|---|---|---|
| `kFeedbackFreshnessMs` | 400 ms | snapshot older than this is stale |
| `kFeedbackMinFailuresForLatch` | 3 | consecutive read failures before a feedback-class STALL may latch |
| `kStallTargetErrorDeg` | 3.0° | below this target error no stall is judged |
| `kStallProgressDeltaDeg` | 0.5° | movement that refreshes the progress window |
| `kStallNoProgressWindowMs` | 700 ms | no-progress window measured from `command_applied_ms` |
| `kPostWakeGraceMs` | 1200 ms | post-wake window that defers failure-count latching (see below) |

- One feedback read failure: `consecutive_failures++`, `last_error` set, logged
  `servo_io feedback failed`, no latch.
- ≥3 consecutive failures *and* stale snapshot: feedback-class STALL latch.
- Transient failures while still fresh: `degraded` status only.
- Feedback-freeze HIL injection suppresses publishes (failures accumulate),
  which exercises the same latch path as a real persistent bus failure.

Measured behaviour addendum (HIL evidence, `artifacts/scs-runtime-io/`): a servo
pair that slept VM-off for minutes answers its first post-wake reads
intermittently for roughly 0.5–1 s, even after two successful spaced wake
probes. Two mitigations, both HIL-verified:

1. The light wake requires two successful feedback reads spaced 100 ms before
   the pair is declared ready.
2. For `kPostWakeGraceMs` after a wake, failure-count latching is deferred
   (logged as degraded); the independent 700 ms no-progress monitor still
   latches a genuinely unresponsive servo, so the grace cannot mask a real
   stall. Failure counters are reset on every power-down transition
   (`finish_stopping` and the emergency path) so a rail-down leftover cannot
   keep the latch alive.

## 10. Stall timing, threshold, latching

- Timing starts at `command_applied_ms` (post-write), never at request time —
  a blocked UART or a power-up sequence cannot consume the stall budget.
- Monitoring requires: active generation, applied, feedback fresh, target error
  ≥ 3°. Expiry/cancel resets the monitor; a new generation restarts it.
- Progress (≥0.5° yaw or pitch) refreshes the window; 700 ms without progress
  at ≥3° error latches STALL, which stops output, releases torque and cuts VM.
- Power-on/wake (`PoweringOn`) is never monitored as motion.
- Thresholds may only be re-tuned with logged evidence, never per-run.

## 11. FreeRTOS core, priority, period, synchronization

- Cores/priorities: safety 0/20, servo_io 1/12, graph 1/5, main 0/1. The safety
  task now preempts everything for microseconds only.
- Periods: safety 20 ms absolute (`vTaskDelayUntil`); servo_io free loop with
  10 ms tick (FreeRTOS runs at 100 Hz) and per-state sample periods (30 ms
  tracking / 100 ms idle); graph 100 ms.
- Synchronization: three seqlocks (mailbox, output request, snapshot) each with
  a single writer; `std::atomic<uint32_t>` for cancel/emergency generations,
  injection flags, torque/vm flags; one FreeRTOS mutex inside `Py32IoExpander`.
  No locks are held across UART transactions; the safety task takes no locks
  except its existing `portMUX` flag guard (microseconds).
- Console discipline (HIL finding): the default USJ TX ring is 256 bytes, so
  logging wedges a task holding the stdout lock once the host stops reading,
  which then blocks the gateway ACK path. The driver rings are sized 4 KiB TX /
  1 KiB RX, and diagnostics go through `hil_log`, which drops lines when the
  USB host is gone or silent (protocol ACKs are never gated). The HIL build
  also emits a 2 Hz `safety hb` liveness line and `safety stall ... source=`
  diagnostics.

## 12. Startup, pause, link loss, fault clear, recovery

- Startup: board.begin() cold self-test (unchanged log/evidence) → tasks start;
  io task initial state `ReadyTorqueOff` (VM on, torque off) → first command
  takes the fast path; `Faulted` if boot self-test failed.
- Pause (command or touch): cancel current generation, `Stop{power_cut=true}` →
  torque off + VM_EN off (policy: pause cuts VM). Resume + new command goes
  through the light wake path.
- Link loss (1.5 s without host): same as pause plus cancel-all; hello
  re-establishes the link; no old command auto-resumes.
- clear_fault (local_confirmation required): clears latched safety faults,
  publishes `Reset`; io task leaves `Faulted` → `PowerOff` with
  `full_test_next`; the old command remains cancelled; nothing moves until a
  new command is submitted.
- Emergency stop: section 7; recovery requires clear_fault then a fresh command
  (full self-test).

## 13. Verification matrix

Host (new `servo_io_tests`, plus all existing suites): the 17 regressions
listed in the acceptance brief — ACK path unaffected by 1 s blocked servo I/O;
no blocking servo API in the safety tick; stall timing starts at
`command_applied_ms`; applied-only-after-write; 1–2 feedback failures tolerated;
≥3 failures + stale latches STALL; 700 ms no-progress latch; 0.5° progress
refresh; expiry/cancel semantics; pause/link-loss/emergency/clear-fault
generation semantics; stale-result non-overwrite; snapshot monotonicity; hard
limit never reaches the servo mailbox; safety period independent of slow UART.

Target build: ESP-IDF 5.5.4 HIL profile, ESP32-S3, app partition flash.

Real HIL (tools/phase1_hil.py, staged):

| Stage | Gate |
|---|---|
| A startup | no checksum mismatch, ping/feedback/torque_off = 1, motion=enabled, board=ready, torque off |
| B protocol | hello.device, stable status, ACK latency logged and < 50 ms device-side |
| C motion | yaw 1.56→12, pitch 45→52 with trajectory samples at ~0.2/0.5/1/2 s, torque on during motion, no false STALL, final error within 2° |
| D pause | pause ACK, paused=true, torque=false, VM off per policy, no further motion |
| E home | resume+home reaches ≈0°/45° without STALL |
| F fault matrix | transient failures (no latch), frozen feedback (latch ~0.5 s), hard limit, emergency (<100 ms to torque-free status), link loss, clear_fault, no auto-resume after reconnect |
| G full runner | `python3 tools/phase1_hil.py --port /dev/cu.usbmodemXXXX --allow-hardware` PASS, then supervised `--require-touch`, then read-only soak (8 h only on explicit request) |
