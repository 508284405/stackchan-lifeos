#!/usr/bin/env python3
"""Safe, read-only USB CDC protocol smoke runner for StackChan HIL.

The default is dry-run.  A real serial port is opened only with ``--port``;
this tool never flashes, resets, sends motion commands, or sends emergency-stop.
"""
from __future__ import annotations

import argparse
import json
import os
import select
import sys
import termios
import time
import tty
from pathlib import Path

SCHEMA = "lifeos.v1"
MAX_LINE = 16 * 1024
BANNER = b"LIFEOS_HIL_READY"


def envelope(kind: str, typ: str, event_id: str, seq: int, payload: dict,
             device_id: str | None = "stackchan-01") -> bytes:
    value = {"schema": SCHEMA, "kind": kind, "type": typ,
             "event_id": event_id, "seq": seq, "ts_ms": int(time.time() * 1000),
             "payload": payload}
    if device_id is not None:
        value["device_id"] = device_id
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


def validate(line: bytes) -> dict | None:
    if len(line.rstrip(b"\r\n")) > MAX_LINE:
        return None
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    required = {"schema", "kind", "type", "event_id", "seq", "ts_ms", "payload"}
    if not isinstance(value, dict) or not required.issubset(value):
        return None
    if value["schema"] != SCHEMA or value["kind"] not in {"event", "command", "ack", "error", "hello"}:
        return None
    if not isinstance(value["event_id"], str) or not value["event_id"]:
        return None
    return value


def dry_run() -> int:
    print(json.dumps({"mode": "dry-run", "actions": ["hello.host", "command.control(status)"],
                      "blocked": True, "reason": "no --port supplied"}))
    return 0


def run(port: str, timeout: float) -> int:
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        print(json.dumps({"mode": "hil", "port": port, "responses": [],
                          "blocked": True, "reason": f"serial.open:{exc}"}))
        return 2
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

        data = bytearray()
        responses: list[dict] = []
        banner_seen = False

        def pump(seconds: float, stop_on_banner: bool = False) -> None:
            nonlocal banner_seen
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                ready, _, _ = select.select([fd], [], [],
                                            min(0.1, max(0.0, end - time.monotonic())))
                if not ready:
                    continue
                data.extend(os.read(fd, 4096))
                while b"\n" in data:
                    raw, _, rest = data.partition(b"\n")
                    data[:] = rest
                    if BANNER in raw:
                        banner_seen = True
                        if stop_on_banner:
                            return
                    value = validate(raw)
                    if value is not None:
                        responses.append(value)

        # Opening a USB CDC port asserts DTR, which resets the ESP32-S3
        # USB Serial/JTAG peripheral (rst:0x15). Drain the boot banner first so
        # the handshake is not swallowed by the reboot; when the target was
        # already running this is just a grace window.
        pump(max(timeout, 3.0), stop_on_banner=True)

        # Only handshake and status are sent. Both are non-actuating/read-only.
        # The first hello can be consumed by the reset above, so the idempotent
        # envelope is retried once with unchanged event_id and seq.
        for _ in range(2):
            os.write(fd, envelope("hello", "hello.host", "hil-hello-1", 1,
                                  {"protocol_versions": [SCHEMA], "session_nonce": "hil-readonly",
                                   "capabilities": ["status"], "raw_media": False}))
            pump(timeout)
            if any(item["kind"] == "hello" for item in responses):
                break
        if any(item["kind"] == "hello" for item in responses):
            os.write(fd, envelope(
                "command", "command.control", "hil-status-1", 2,
                {"action": "status"}))
            pump(timeout)
        hello_ok = any(
            item["kind"] == "hello"
            and item.get("payload", {}).get("firmware", "").startswith("lifeos-phase1-hil-")
            and item.get("payload", {}).get("motion_enabled") is False
            for item in responses
        )
        ok = hello_ok and any(item["kind"] in {"ack", "error"} for item in responses)
        print(json.dumps({"mode": "hil", "port": port, "responses": responses,
                          "blocked": not ok,
                          "reason": None if ok else "no confirmed lifeos.v1 hello/status response"}))
        return 0 if ok else 2
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB CDC device; omission keeps dry-run mode")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    return dry_run() if not args.port else run(args.port, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
