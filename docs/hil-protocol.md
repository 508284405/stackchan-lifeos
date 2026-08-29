# USB CDC HIL protocol runner

`tools/hil_usb_runner.py` is a deliberately narrow, read-only smoke runner for
Phase 1. It defaults to dry-run and does not open a serial device unless
`--port` is explicitly supplied. It has no flash, reset, motion, media, or
emergency-stop operation.

```sh
python3 tools/hil_usb_runner.py
python3 tools/hil_usb_runner.py --port /dev/cu.usbmodem101 --timeout 3
```

The live path configures 115200 8N1 and first drains the boot banner
(`LIFEOS_HIL_READY`) or waits out a grace window: on macOS the kernel CDC-ACM
driver asserts DTR when the port is opened, which the ESP32-S3 USB-Serial/JTAG
peripheral interprets as `rst:0x15 (USB_UART_CHIP_RESET)`, so traffic sent
immediately after open is consumed by a reboot. The idempotent `hello.host`
envelope (fixed `event_id`, `seq`) is then sent and retried once with identical
content; afterwards the read-only `command.control`/`status` command is sent.
The runner accepts only valid `lifeos.v1` JSONL responses. Success requires
both a hello response (marker `lifeos-phase1-hil-*`, `motion_enabled` false)
and an ACK/error response. If no response confirms a running StackChan protocol
firmware, the runner exits with `blocked` status and makes no further attempt.

The status probe intentionally omits command TTL fields because the USB host and
device monotonic clocks are not synchronized before the hello exchange. TTL is
covered with a shared virtual clock in the replay and firmware tests; real
actuating commands must add an explicit clock-offset/session policy first.

Before using a real port, verify the USB identity and firmware out of band.
The runner cannot prove the target's hardware identity from a generic serial
path alone. Verified 2026-08-29 against the LifeOS HIL image on ESP32-S3 rev 0.2
(MAC `1c:db:d4:ba:43:40`): hello + completed status ACK, and rejection of
duplicate commands, sequence gaps, expired TTL and unknown actions.
