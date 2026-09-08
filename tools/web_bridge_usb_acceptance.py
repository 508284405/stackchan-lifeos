#!/usr/bin/env python3
"""Run a read-only real USB link check through the Web Bridge service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from bridge import Bridge, SQLiteStore
from bridge.domain import CommandState, DeviceSession, DiscoveryCandidate, SessionState
from bridge.transports.serial import UsbSerialTransport


async def run_acceptance(
    port: str,
    *,
    device_id: str,
    hardware_id: str,
    startup_grace_s: float,
    handshake_timeout_s: float,
    command_timeout_s: float,
) -> dict[str, Any]:
    bridge = Bridge(SQLiteStore())
    transport = UsbSerialTransport(
        port,
        transport_id=f"usb:{port}",
        startup_grace_s=startup_grace_s,
    )
    candidate = DiscoveryCandidate(
        candidate_id=f"candidate-{hardware_id}",
        hardware_id=hardware_id,
        device_id=device_id,
        transport_id=transport.transport_id,
        capabilities=frozenset({"status", "protocol", "safety"}),
    )
    bridge.discover(candidate)
    bridge.claim(candidate.candidate_id)
    session: DeviceSession | None = None
    try:
        session = await bridge.connect(device_id, transport)
        deadline = asyncio.get_running_loop().time() + handshake_timeout_s
        while asyncio.get_running_loop().time() < deadline:
            current = bridge.active_session_for_device(device_id)
            if current is not None and current.state is SessionState.ONLINE:
                session = current
                break
            await asyncio.sleep(0.05)
        if session.state is not SessionState.ONLINE:
            return {
                "status": "FAIL",
                "mode": "web-bridge-usb-read-only",
                "port": port,
                "device_id": device_id,
                "hardware_id": hardware_id,
                "session_state": session.state.value,
                "safe_idle": False,
                "motion_command_sent": False,
                "flash_or_erase_performed": False,
                "reason": "device hello was not accepted before handshake timeout",
            }
        command = await bridge.submit_command(device_id, "control.status")
        deadline = asyncio.get_running_loop().time() + command_timeout_s
        while asyncio.get_running_loop().time() < deadline:
            current_command = bridge.get_command(command.command_id)
            if current_command.state in {
                CommandState.COMPLETED,
                CommandState.REJECTED,
                CommandState.SAFETY_BLOCKED,
                CommandState.OFFLINE,
                CommandState.TIMEOUT,
                CommandState.EXPIRED,
            }:
                command = current_command
                break
            await asyncio.sleep(0.05)
        command = bridge.get_command(command.command_id)
        result = command.result or {}
        safe_idle = (
            command.state is CommandState.COMPLETED
            and result.get("status") == "completed"
            and result.get("torque_enabled") is False
            and result.get("fault") is False
        )
        return {
            "status": "PASS" if session.state is SessionState.ONLINE and safe_idle else "FAIL",
            "mode": "web-bridge-usb-read-only",
            "port": port,
            "device_id": device_id,
            "hardware_id": hardware_id,
            "session_state": session.state.value,
            "session_id": session.session_id,
            "command_id": command.command_id,
            "command_state": command.state.value,
            "status_result": result,
            "safe_idle": safe_idle,
            "motion_command_sent": False,
            "flash_or_erase_performed": False,
        }
    finally:
        if session is not None:
            await bridge.disconnect(session.session_id)
        else:
            await transport.close()
        bridge.store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--device-id", default="stackchan-01")
    parser.add_argument("--hardware-id", default="1c:db:d4:ba:43:40")
    parser.add_argument("--startup-grace", type=float, default=3.0)
    parser.add_argument("--handshake-timeout", type=float, default=5.0)
    parser.add_argument("--command-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            run_acceptance(
                args.port,
                device_id=args.device_id,
                hardware_id=args.hardware_id,
                startup_grace_s=args.startup_grace,
                handshake_timeout_s=args.handshake_timeout,
                command_timeout_s=args.command_timeout,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "mode": "web-bridge-usb-read-only", "reason": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
