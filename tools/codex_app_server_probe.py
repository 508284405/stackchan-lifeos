#!/usr/bin/env python3
"""Probe the local Codex app-server initialize/reconnect seam.

The probe deliberately sends only ``initialize`` and the matching
``initialized`` notification. It never starts a thread or turn and never
forwards the app-server protocol to the device gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import subprocess
import time
from typing import Any


def _read_json_line(process: subprocess.Popen[bytes], timeout: float) -> dict[str, Any]:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buffered = bytearray()
    try:
        while time.monotonic() < deadline:
            events = selector.select(max(0.0, deadline - time.monotonic()))
            if not events:
                break
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            buffered.extend(chunk)
            while b"\n" in buffered:
                raw, _, rest = buffered.partition(b"\n")
                buffered[:] = rest
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and "id" in value:
                    return value
    finally:
        selector.close()
    raise RuntimeError(f"app-server produced no JSON response within {timeout}s: {buffered!r}")


def _handshake(codex: str, timeout: float) -> dict[str, Any]:
    process = subprocess.Popen(
        [codex, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        request = {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "stackchan-lifeos-contract",
                    "title": "StackChan LifeOS contract probe",
                    "version": "0",
                }
            },
        }
        process.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
        process.stdin.flush()
        response = _read_json_line(process, timeout)
        if response.get("id") != 1 or not isinstance(response.get("result"), dict):
            raise RuntimeError(f"unexpected initialize response: {response}")
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        return response
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--repeat", type=int, default=2,
                        help="independent initialize/close handshakes")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.timeout <= 0:
        parser.error("--repeat must be >= 1 and --timeout must be positive")
    codex = shutil.which("codex")
    if codex is None:
        print("FAIL codex executable not found")
        return 1
    try:
        responses = [_handshake(codex, args.timeout) for _ in range(args.repeat)]
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL {error}")
        return 1
    agents = [response["result"].get("userAgent") for response in responses]
    print(f"PASS app-server initialize/initialized/close handshakes={len(responses)}")
    print(f"PASS user_agents={agents}")
    print("PASS no thread/start, turn/start, tool call, or device command sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
