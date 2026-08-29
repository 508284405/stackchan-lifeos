# USB CDC HIL protocol runner

`tools/hil_usb_runner.py` is a deliberately narrow, read-only smoke runner for
Phase 1. It defaults to dry-run and does not open a serial device unless
`--port` is explicitly supplied. It has no flash, reset, motion, media, or
emergency-stop operation.

```sh
python3 tools/hil_usb_runner.py
python3 tools/hil_usb_runner.py --port /dev/cu.usbmodem101 --timeout 3
```

The live path configures 115200 8N1, sends `hello.host`, then the read-only
`command.control`/`status` command and accepts only valid
`lifeos.v1` JSONL responses. Success requires both a hello response and an
ACK/error response. If no response confirms a running StackChan protocol
firmware, the runner exits with `blocked` status and makes no further attempt.

The status probe intentionally omits command TTL fields because the USB host and
device monotonic clocks are not synchronized before the hello exchange. TTL is
covered with a shared virtual clock in the replay and firmware tests; real
actuating commands must add an explicit clock-offset/session policy first.

Before using a real port, verify the USB identity and firmware out of band.
The runner cannot prove the target's hardware identity from a generic serial
path alone.
