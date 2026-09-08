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
             device_id: str | None = "stackchan-01",
             correlation_id: str | None = None) -> bytes:
    value = {"schema": SCHEMA, "kind": kind, "type": typ,
             "event_id": event_id}
    if correlation_id is not None:
        value["correlation_id"] = correlation_id
    if device_id is not None:
        value["device_id"] = device_id
    value.update({"seq": seq, "ts_ms": int(time.time() * 1000), "payload": payload})
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

        # Opening a USB CDC port may assert DTR and reset the ESP32-S3
        # USB Serial/JTAG peripheral. Always drain the complete grace window;
        # returning as soon as the banner appears can race the app main loop
        # and let a status frame reach the gateway before hello is installed.
        pump(max(timeout, 3.0), stop_on_banner=False)

        # Only handshake and status are sent. Both are non-actuating/read-only.
        # The first hello can be consumed by the reset above, so the idempotent
        # envelope is retried once with unchanged event_id and seq. Host
        # sequences start at one, matching SessionManager and the wire examples.
        run_token = time.monotonic_ns()
        hello_event_id = f"hil-hello-{run_token}"
        status_event_id = f"hil-status-{run_token}"
        session_nonce = f"hil-readonly-{run_token}"
        responses.clear()
        hello_response = None
        for _ in range(2):
            os.write(fd, envelope("hello", "hello.host", hello_event_id, 1,
                                  {"protocol_versions": [SCHEMA], "session_nonce": session_nonce,
                                   "capabilities": ["status"], "media_enabled": False}))
            pump(timeout)
            hello_response = next(
                (
                    item
                    for item in responses
                    if item.get("kind") == "hello"
                    and item.get("type") == "hello.device"
                    and item.get("correlation_id") == hello_event_id
                ),
                None,
            )
            if hello_response is not None:
                break
        if hello_response is not None:
            os.write(fd, envelope(
                "command", "command.control", status_event_id, 2,
                {"action": "status"}, correlation_id=status_event_id))
            pump(timeout)
        hello_payload = hello_response.get("payload", {}) if hello_response else {}
        hello_ok = (
            hello_response is not None
            and hello_payload.get("firmware", "").startswith("lifeos-phase1-")
            and hello_payload.get("board") == "StackChan/CoreS3"
            and SCHEMA in hello_payload.get("protocol_versions", [])
            and isinstance(hello_payload.get("motion_enabled"), bool)
            and hello_payload.get("device_id", hello_response.get("device_id")) == "stackchan-01"
        )
        status_response = next(
            (
                item
                for item in responses
                if item.get("kind") == "ack"
                and item.get("type") == "ack.command"
                and item.get("correlation_id") == status_event_id
            ),
            None,
        )
        status_payload = status_response.get("payload", {}) if status_response else {}
        status_ok = (
            status_response is not None
            and status_payload.get("status") == "completed"
            and status_payload.get("torque_enabled") is False
            and status_payload.get("fault") is False
        )
        ok = hello_ok and status_ok
        print(json.dumps({"mode": "hil", "port": port, "responses": responses,
                          "blocked": not ok,
                          "identity": {
                              "device_id": hello_response.get("device_id") if hello_response else None,
                              "board": hello_payload.get("board"),
                              "mac": hello_payload.get("mac"),
                              "firmware": hello_payload.get("firmware"),
                              "protocol": SCHEMA,
                          },
                          "safe_idle": status_ok,
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
