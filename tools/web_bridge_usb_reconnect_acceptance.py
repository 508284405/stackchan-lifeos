#!/usr/bin/env python3
"""Run a controlled real USB disconnect/reconnect acceptance through Bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from bridge import Bridge, SQLiteStore
from bridge.domain import CommandState, DeviceSession, DiscoveryCandidate, SessionState
from bridge.transports.serial import UsbSerialTransport


TERMINAL_STATES = {
    CommandState.COMPLETED,
    CommandState.REJECTED,
    CommandState.SAFETY_BLOCKED,
    CommandState.OFFLINE,
    CommandState.TIMEOUT,
    CommandState.EXPIRED,
}


async def wait_online(bridge: Bridge, device_id: str, timeout_s: float) -> DeviceSession:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        session = bridge.active_session_for_device(device_id)
        if session is not None and session.state is SessionState.ONLINE:
            return session
        await asyncio.sleep(0.05)
    session = bridge.latest_session_for_device(device_id)
    state = session.state.value if session is not None else "missing"
    raise RuntimeError(f"device hello was not accepted before timeout: {state}")


async def wait_command(bridge: Bridge, command_id: str, timeout_s: float):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        command = bridge.get_command(command_id)
        if command.state in TERMINAL_STATES:
            return command
        await asyncio.sleep(0.05)
    return bridge.get_command(command_id)


async def run_acceptance(
    port: str,
    *,
    device_id: str,
    hardware_id: str,
    startup_grace_s: float,
    disconnect_seconds: float,
    timeout_s: float,
) -> dict[str, Any]:
    bridge = Bridge(SQLiteStore())
    candidate = DiscoveryCandidate(
        candidate_id=f"candidate-{hardware_id}",
        hardware_id=hardware_id,
        device_id=device_id,
        transport_id=f"usb:{port}",
        capabilities=frozenset({"status", "protocol", "safety"}),
    )
    bridge.discover(candidate)
    bridge.claim(candidate.candidate_id)
    first_transport = UsbSerialTransport(
        port,
        transport_id=f"usb:{port}:first",
        startup_grace_s=startup_grace_s,
    )
    second_transport: UsbSerialTransport | None = None
    first_session: DeviceSession | None = None
    second_session: DeviceSession | None = None
    try:
        await bridge.connect(device_id, first_transport)
        first_session = await wait_online(bridge, device_id, timeout_s)
        first_command = await bridge.submit_command(device_id, "control.status")
        first_command = await wait_command(bridge, first_command.command_id, timeout_s)

        await bridge.disconnect(first_session.session_id)
        await asyncio.sleep(disconnect_seconds)

        second_transport = UsbSerialTransport(
            port,
            transport_id=f"usb:{port}:reconnected",
            startup_grace_s=startup_grace_s,
        )
        await bridge.connect(device_id, second_transport)
        second_session = await wait_online(bridge, device_id, timeout_s)
        second_command = await bridge.submit_command(device_id, "control.status")
        second_command = await wait_command(bridge, second_command.command_id, timeout_s)

        first_result = first_command.result or {}
        second_result = second_command.result or {}
        first_safe = (
            first_command.state is CommandState.COMPLETED
            and first_result.get("torque_enabled") is False
            and first_result.get("fault") is False
        )
        second_safe = (
            second_command.state is CommandState.COMPLETED
            and second_result.get("torque_enabled") is False
            and second_result.get("fault") is False
        )
        no_replay = (
            first_session.session_id != second_session.session_id
            and first_command.session_id == first_session.session_id
            and second_command.session_id == second_session.session_id
        )
        return {
            "status": "PASS" if first_safe and second_safe and no_replay else "FAIL",
            "mode": "web-bridge-usb-controlled-reconnect",
            "port": port,
            "device_id": device_id,
            "hardware_id": hardware_id,
            "first_session": {
                "session_id": first_session.session_id,
                "state": first_session.state.value,
                "transport_id": first_session.transport_id,
            },
            "first_status": {
                "command_id": first_command.command_id,
                "state": first_command.state.value,
                "result": first_result,
            },
            "disconnect_seconds": disconnect_seconds,
            "second_session": {
                "session_id": second_session.session_id,
                "state": second_session.state.value,
                "transport_id": second_session.transport_id,
            },
            "second_status": {
                "command_id": second_command.command_id,
                "state": second_command.state.value,
                "result": second_result,
            },
            "checks": {
                "first_safe_idle": first_safe,
                "second_safe_idle": second_safe,
                "new_session_after_reconnect": first_session.session_id != second_session.session_id,
                "old_status_not_replayed": no_replay,
                "motion_command_sent": False,
                "flash_or_erase_performed": False,
            },
        }
    finally:
        if second_session is not None:
            await bridge.disconnect(second_session.session_id)
        else:
            await first_transport.close()
        if second_transport is not None and second_session is None:
            await second_transport.close()
        bridge.store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--device-id", default="stackchan-01")
    parser.add_argument(
        "--hardware-id",
        required=True,
        help="hardware ID reported by the device scan/hello response",
    )
    parser.add_argument("--startup-grace", type=float, default=3.0)
    parser.add_argument("--disconnect-seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=6.0)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            run_acceptance(
                args.port,
                device_id=args.device_id,
                hardware_id=args.hardware_id,
                startup_grace_s=args.startup_grace,
                disconnect_seconds=args.disconnect_seconds,
                timeout_s=args.timeout,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "mode": "web-bridge-usb-controlled-reconnect", "reason": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
