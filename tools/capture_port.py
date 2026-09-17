#!/usr/bin/env python3
"""Capture raw serial output from the LifeOS device for HIL evidence.

Usage: capture_port.py PORT SECONDS OUTPUT [--reset]

--reset asks esptool to run chip_id with --after hard_reset, which reboots the
device into the application while this script holds the port open, so the
startup banner is captured. esptool is resolved from --esptool,
LIFEOS_ESPTOOL, or the current Python environment.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import termios
import time

def open_port(port: str) -> tuple[int, list]:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    tty = termios.tcgetattr(fd)
    tty[0] = 0
    tty[1] = 0
    tty[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    tty[3] = 0
    tty[4] = termios.B115200
    tty[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, tty)
    return fd, old


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("seconds", type=float)
    parser.add_argument("output")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--esptool",
        default=os.environ.get("LIFEOS_ESPTOOL"),
        help=(
            "esptool executable or .py path; defaults to `python -m esptool` "
            "(or LIFEOS_ESPTOOL)"
        ),
    )
    args = parser.parse_args()

    fd, old = open_port(args.port)
    chunks: list[bytes] = []
    try:
        if args.reset:
            # Reboot into the application while this process owns the port.
            # esptool needs the port briefly; close, reset, reopen.
            termios.tcsetattr(fd, termios.TCSANOW, old)
            os.close(fd)
            esptool_command = (
                [sys.executable, args.esptool]
                if args.esptool and args.esptool.endswith(".py")
                else [args.esptool]
                if args.esptool
                else [sys.executable, "-m", "esptool"]
            )
            subprocess.run(
                [*esptool_command, "--chip", "esp32s3", "--port", args.port,
                 "--baud", "115200", "--before", "no_reset", "--after", "hard_reset",
                 "chip_id"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            fd, old = open_port(args.port)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(fd, 8192)
            except BlockingIOError:
                continue
            if data:
                chunks.append(data)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)

    raw = b"".join(chunks)
    with open(args.output, "wb") as handle:
        handle.write(raw)
    text = raw.decode("utf-8", errors="replace")
    print(text)
    summary = {
        "output": args.output,
        "bytes": len(raw),
        "lines": len(text.splitlines()),
        "has_banner": "LIFEOS_HIL_READY" in text,
        "has_checksum_mismatch": "checksum mismatch" in text.lower(),
    }
    print(json.dumps(summary), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
