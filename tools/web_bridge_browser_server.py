#!/usr/bin/env python3
"""Local fake-device server used only for browser black-box acceptance."""

from __future__ import annotations

import argparse
import asyncio

import uvicorn

from bridge import Bridge, FakeTransport
from bridge.api import create_app


def build_app():
    bridge = Bridge(
        feature_gates={"usb_add": True, "media": True, "behavior": True, "speech": True}
    )
    transport = FakeTransport(
        capabilities={
            "status",
            "control",
            "motion",
            "safety",
            "protocol",
            "touch",
            "imu",
            "display",
            "camera",
            "behavior",
            "speech",
        }
    )
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id, display_name="Browser Fake StackChan")
    asyncio.run(bridge.connect(device.device_id, transport))
    asyncio.run(transport.emit_heartbeat(uptime_ms=12_345))
    app = create_app(bridge)
    app.state.browser_test_bridge = bridge
    app.state.browser_test_transport = transport
    return app


app = build_app()


@app.post("/__test__/disconnect")
async def test_disconnect():
    bridge = app.state.browser_test_bridge
    session = bridge.active_session_for_device("stackchan-fake-01")
    if session is not None:
        await bridge.disconnect(session.session_id)
    return {"state": "offline"}


@app.post("/__test__/reconnect")
async def test_reconnect():
    bridge = app.state.browser_test_bridge
    transport = FakeTransport(
        device_id="stackchan-fake-01",
        hardware_id="fake-hw-01",
        transport_id="fake-usb-reconnected",
        capabilities={
            "status",
            "control",
            "motion",
            "safety",
            "protocol",
            "touch",
            "imu",
            "display",
            "camera",
            "behavior",
            "speech",
        },
    )
    session = await bridge.connect("stackchan-fake-01", transport)
    await transport.emit_heartbeat(uptime_ms=12_346)
    app.state.browser_test_transport = transport
    return {"state": session.state.value, "session_id": session.session_id}


@app.post("/__test__/health-fault")
async def test_health_fault():
    transport = app.state.browser_test_transport
    await transport.emit(
        kind="event",
        type="health.report",
        payload={"heap": 99_999, "uptime_ms": 12_347, "fault": True, "faults": []},
    )
    return {"fault": True}


@app.post("/__test__/health-clear")
async def test_health_clear():
    transport = app.state.browser_test_transport
    await transport.emit(
        kind="event",
        type="health.report",
        payload={"heap": 99_998, "uptime_ms": 12_348, "fault": False, "faults": []},
    )
    return {"fault": False}


# create_app mounts the static web root before this test-only extension is
# declared. Put the mount last so the private injection routes are reachable
# during black-box acceptance without changing the production API.
_static_mount = next(
    (route for route in app.router.routes if getattr(route, "name", None) == "web"),
    None,
)
if _static_mount is not None:
    app.router.routes.remove(_static_mount)
    app.router.routes.append(_static_mount)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
