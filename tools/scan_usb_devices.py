#!/usr/bin/env python3
"""Enumerate USB serial ports that look like StackChan devices.

CLI wrapper around :mod:`bridge.scan` — the same probe core the Web Bridge
"scan and add" API uses. Default mode only lists candidate /dev nodes and
never opens them. With ``--probe`` each port is opened briefly for a
read-only lifeos.v1 ``hello.host`` handshake to collect device identity
(device_id, MAC, board, firmware). Opening a USB CDC port asserts DTR and
resets the ESP32-S3 Serial/JTAG peripheral, so close other sessions
(``web_bridge_real_server``, ``hil_usb_runner``) before probing.

Run from the repository root with ``PYTHONPATH=.``.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys


def suggest_command(result: dict) -> str | None:
    identity = result["identity"]
    if not identity or not identity.get("device_id"):
        return None
    return (
        "PYTHONPATH=. .venv/bin/python tools/web_bridge_real_server.py"
        f" --usb-port {result['path']}"
        f" --device-id {identity['device_id']}"
        + (f" --hardware-id {identity['hardware_id']}" if identity.get("hardware_id") else "")
    )


def render(results: list[dict]) -> list[str]:
    lines = []
    for result in results:
        cell = f"  {result['path']:<28} {result['state']}"
        identity = result["identity"]
        if identity:
            fields = [f"{key}={value}" for key, value in identity.items() if value]
            cell += "  " + "  ".join(fields)
        elif result["reason"]:
            cell += f"  ({result['reason']})"
        lines.append(cell)
    return lines


def run(argv: list[str] | None = None) -> int:
    from bridge.scan import PORT_PATTERNS, candidate_ports, probe_port

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="open each candidate port for a read-only hello handshake "
        "(resets the ESP32-S3 CDC peripheral; close other sessions first)",
    )
    parser.add_argument("--timeout", type=float, default=2.0, help="probe reply timeout (s)")
    parser.add_argument("--grace", type=float, default=3.0, help="post-open boot grace (s)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    paths = candidate_ports()
    if not paths:
        system = platform.system()
        print(
            f"no USB serial candidates found on {system} "
            f"(patterns: {', '.join(PORT_PATTERNS.get(system, ['none']))})"
        )
        return 1

    results = [
        probe_port(path, timeout_s=args.timeout, grace_s=args.grace) if args.probe
        else {"path": path, "state": "unprobed", "identity": None, "reason": None}
        for path in paths
    ]

    if args.json:
        print(json.dumps({"mode": "probe" if args.probe else "list", "ports": results}))
    else:
        print(f"USB serial candidates ({platform.system()}):")
        print("\n".join(render(results)))
        suggestions = [command for command in map(suggest_command, results) if command]
        if suggestions:
            print("\nsuggested:")
            print("\n".join(f"  {command}" for command in suggestions))
        if args.probe:
            print(
                "\nnote: probing asserted DTR on each port; any previously "
                "connected session must re-establish hello"
            )

    online = any(result["state"] == "online" for result in results)
    return 0 if online or not args.probe else 1


if __name__ == "__main__":
    raise SystemExit(run())
