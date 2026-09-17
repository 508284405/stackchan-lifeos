#!/usr/bin/env python3
"""Serve the Web Console against one real USB device with fixed live capabilities."""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from bridge import Bridge, SQLiteStore
from bridge.api import create_app
from bridge.domain import CommandState, DeviceSession, DiscoveryCandidate, SessionState
from bridge.transports.serial import UsbSerialTransport
from bridge.manual_evidence import load_manual_control_evidence


TERMINAL_STATES = {
    CommandState.COMPLETED,
    CommandState.REJECTED,
    CommandState.SAFETY_BLOCKED,
    CommandState.OFFLINE,
    CommandState.TIMEOUT,
    CommandState.EXPIRED,
}

# This launcher is the production USB entry point, not a generic Bridge
# factory. It must never quietly turn a live capability into a fake or
# read-only one based on an omitted command-line flag.
REAL_CAMERA_PREVIEW_ENABLED = True
REAL_MANUAL_PREVIEW_FPS = 2
RECONNECT_INITIAL_DELAY_S = 0.5
RECONNECT_MAX_DELAY_S = 8.0
logger = logging.getLogger(__name__)


async def wait_online(bridge: Bridge, device_id: str, timeout_s: float) -> DeviceSession:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    next_hello_retry = loop.time() + 0.75
    while loop.time() < deadline:
        session = bridge.active_session_for_device(device_id)
        if session is not None and session.state is SessionState.ONLINE:
            return session
        if loop.time() >= next_hello_retry:
            await bridge.retry_host_hello(device_id)
            next_hello_retry = loop.time() + 0.75
        await asyncio.sleep(0.05)
    raise RuntimeError("real device hello was not accepted before timeout")


async def wait_command(bridge: Bridge, command_id: str, timeout_s: float):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        command = bridge.get_command(command_id)
        if command.state in TERMINAL_STATES:
            return command
        await asyncio.sleep(0.05)
    return bridge.get_command(command_id)


def build_app(
    *,
    port: str,
    device_id: str,
    hardware_id: str,
    startup_grace_s: float,
    manual_control_evidence: str | None = None,
):
    evidence = (
        load_manual_control_evidence(
            manual_control_evidence,
            device_id=device_id,
            hardware_id=hardware_id,
        )
        if manual_control_evidence is not None
        else None
    )
    bridge = Bridge(
        SQLiteStore(),
        feature_gates={
            "usb_add": True,
            "media": REAL_CAMERA_PREVIEW_ENABLED,
            "manual_control_v1": evidence is not None,
            "manual_camera_preview": evidence is not None,
        },
        camera_preview_fps=REAL_MANUAL_PREVIEW_FPS,
    )
    capabilities = {
        "status",
        "protocol",
        "safety",
        "camera",
        "camera_capture_ts_v1",
    }
    if evidence is not None:
        capabilities.update({"manual_control_v1", "manual_video_guard_v1", "manual_preflight_v1"})
    candidate = DiscoveryCandidate(
        candidate_id=f"candidate-{hardware_id}",
        hardware_id=hardware_id,
        device_id=device_id,
        transport_id=f"usb:{port}",
        capabilities=frozenset(capabilities),
    )
    bridge.discover(candidate)
    bridge.claim(candidate.candidate_id)
    app = create_app(bridge)

    def new_transport() -> UsbSerialTransport:
        return UsbSerialTransport(
            port,
            transport_id=f"usb:{port}:web-manual",
            startup_grace_s=startup_grace_s,
            manual_control_verified=evidence is not None,
        )

    async def connect_real_device() -> DeviceSession:
        transport = new_transport()
        session = await bridge.connect(device_id, transport)
        session = await wait_online(bridge, device_id, timeout_s=8.0)
        command = await bridge.submit_command(device_id, "control.status")
        command = await wait_command(bridge, command.command_id, timeout_s=8.0)
        result = command.result or {}
        if (
            session.state is not SessionState.ONLINE
            or command.state is not CommandState.COMPLETED
            or result.get("torque_enabled") is not False
            or result.get("fault") is not False
        ):
            await bridge.disconnect(session.session_id)
            raise RuntimeError("real device did not reach completed safe-idle status")
        if evidence is not None and (
            "manual_control_v1" not in session.capabilities
            or "manual_video_guard_v1" not in session.capabilities
            or "camera_capture_ts_v1" not in session.capabilities
            or bridge.registry.get(device_id).firmware_version != evidence.firmware_version
        ):
            await bridge.disconnect(session.session_id)
            raise RuntimeError("real device did not declare manual_control_v1")
        app.state.real_session_id = session.session_id
        app.state.real_transport = transport
        return session

    async def reconnect_real_device() -> None:
        """Reconnect only after a fully fenced session; never replay control."""

        delay_s = RECONNECT_INITIAL_DELAY_S
        while True:
            await asyncio.sleep(0.25)
            session = bridge.active_session_for_device(device_id)
            if session is not None and session.state in {SessionState.ONLINE, SessionState.DEGRADED}:
                delay_s = RECONNECT_INITIAL_DELAY_S
                continue
            if session is not None:
                await bridge.disconnect(session.session_id)
            try:
                await connect_real_device()
            except Exception as exc:
                logger.warning("real device reconnect failed: %s", exc)
                await asyncio.sleep(delay_s)
                delay_s = min(delay_s * 2, RECONNECT_MAX_DELAY_S)

    @app.on_event("startup")
    async def start_real_device() -> None:
        await connect_real_device()
        app.state.real_reconnect_task = asyncio.create_task(reconnect_real_device())

    @app.on_event("shutdown")
    async def disconnect_real_device() -> None:
        reconnect_task = getattr(app.state, "real_reconnect_task", None)
        if reconnect_task is not None:
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)
        session = bridge.active_session_for_device(device_id)
        if session is not None:
            await bridge.disconnect(session.session_id)
        bridge.store.close()

    app.state.real_bridge = bridge
    return app


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--usb-port", required=True)
    parser.add_argument("--device-id", default="stackchan-01")
    parser.add_argument(
        "--hardware-id",
        required=True,
        help="hardware ID reported by the device scan/hello response",
    )
    parser.add_argument("--startup-grace", type=float, default=3.0)
    parser.add_argument(
        "--manual-control-evidence",
        help="path to a supervised lifeos.manual-hil.v1 PASS record for this exact device/firmware",
    )
    args = parser.parse_args(argv)
    app = build_app(
        port=args.usb_port,
        device_id=args.device_id,
        hardware_id=args.hardware_id,
        startup_grace_s=args.startup_grace,
        manual_control_evidence=args.manual_control_evidence,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
