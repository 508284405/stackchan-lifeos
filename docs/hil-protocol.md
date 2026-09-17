# USB CDC HIL protocol runners

`tools/hil_usb_runner.py` is the deliberately narrow, read-only smoke runner for
the earlier motion-disabled image. It defaults to dry-run and does not open a
serial device unless `--port` is explicitly supplied. It has no flash, reset,
motion, media, or emergency-stop operation.

The complete real-HAL matrix is `tools/phase1_hil.py`. It is separately gated by
`--allow-hardware`, requires the HIL image's maintainer authorization fields,
and is the only runner that exercises supervised motion, emergency stop,
feedback-freeze injection, supervised mechanical-load stall, and the optional
read-only soak. The mechanical-load check is opt-in and requires an operator
to apply only gentle resistance and release it after torque is cut.

```sh
python3 tools/hil_usb_runner.py
python3 tools/hil_usb_runner.py --port /dev/cu.usbmodemXXXX --timeout 3
```

The live path configures 115200 8N1 and first drains the boot banner
(`LIFEOS_HIL_READY`) or waits out a grace window: on macOS the kernel CDC-ACM
driver asserts DTR when the port is opened, which the ESP32-S3 USB-Serial/JTAG
peripheral interprets as `rst:0x15 (USB_UART_CHIP_RESET)`, so traffic sent
immediately after open is consumed by a reboot. The idempotent `hello.host`
envelope (fixed `event_id`, `seq`) is then sent and retried once with identical
content; afterwards the read-only `command.control`/`status` command is sent.
The runner accepts only valid `lifeos.v1` JSONL responses. Success requires a
`hello.device` identifying `StackChan/CoreS3`, a `lifeos-phase1-*` firmware and
the `lifeos.v1` protocol, followed by a completed read-only status ACK with
`torque_enabled=false` and `fault=false`. `motion_enabled` may be true on the
current HIL firmware; this smoke check does not enable motion and is not an
actuator HIL pass. If no response confirms the identity and safe idle state, the
runner exits with `blocked` status and makes no further attempt.

The status probe intentionally omits command TTL fields because the USB host and
device monotonic clocks are not synchronized before the hello exchange. TTL is
covered with a shared virtual clock in the replay and firmware tests; real
actuating commands must add an explicit clock-offset/session policy first.

Error responses carry a machine-readable reason next to the coarse code:
`{"code":"unauthorized","detail":"sequence_rejected"}` for sequence gaps,
`{"code":"unsupported","detail":"unsupported_action"}` for actions the image
does not implement. A structurally valid hello always re-establishes the
session (`docs/protocol.md`: sequence is renegotiated from hello after a
reconnect), so a host that lands on a live gateway — the CDC open reset is
not guaranteed — simply sends hello again; only session bookkeeping is
cleared and safety state (pause, latched faults, torque) is untouched.
Hellos with a mismatched device_id or an unknown type are rejected without
resetting the session. Actuating commands still owe an explicit
clock-offset/TTL session policy before they are enabled.

Before using either runner on a real port, verify the USB identity and firmware
out of band. The runner cannot prove the target's hardware identity from a
generic serial path alone. A read-only smoke was verified 2026-08-30 on
`/dev/cu.usbmodemXXXX`: Espressif USB serial `<device-serial>`,
ESP32-S3/StackChan CoreS3, MAC `<device-mac>`, firmware
`lifeos-phase1-0.3.0`, and `lifeos.v1` hello/status all passed with torque off and
fault false. This is direct protocol evidence only; it does not constitute the
Web Bridge adapter/API E2E or real-HAL actuator HIL. The full actuator matrix
remains separately gated by `tools/phase1_hil.py` and its touch, emergency,
mechanical-stall, and soak requirements.
