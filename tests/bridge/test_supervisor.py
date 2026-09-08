"""Bridge lifecycle supervision and write-fencing regression tests."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from bridge import Bridge, FakeTransport
from bridge.domain import CommandState, LeaseState, SessionState


def _connected_bridge(*, capabilities: set[str] | None = None, auto_ack: bool = True):
    bridge = Bridge(feature_gates={"manual_control_v1": bool(capabilities and "manual_control_v1" in capabilities)})
    transport = FakeTransport(
        capabilities=capabilities or {"status", "health"},
        auto_ack=auto_ack,
        auto_complete=False,
    )
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    return bridge, transport, device


def test_supervisor_sends_general_host_heartbeat_and_keeps_health_session_fresh():
    async def scenario():
        bridge, transport, device = _connected_bridge()
        session = await bridge.connect(device.device_id, transport)
        await bridge.start_supervisor(interval_s=0.05)
        try:
            await asyncio.sleep(0.7)
            return bridge.get_session(session.session_id), list(transport.sent_frames)
        finally:
            await bridge.stop_supervisor()

    session, frames = asyncio.run(scenario())
    heartbeats = [
        frame
        for frame in frames
        if frame.get("kind") == "event" and frame.get("type") == "host.heartbeat"
    ]
    assert session.state is SessionState.ONLINE
    assert heartbeats
    assert all(frame["payload"] == {"media_enabled": False} for frame in heartbeats)


def test_transport_write_failure_fences_session_and_closes_future_writes():
    async def scenario():
        bridge, transport, device = _connected_bridge()
        session = await bridge.connect(device.device_id, transport)
        transport.fail_send = True
        command = await bridge.submit_command(device.device_id, "control.status")
        follow_up = await bridge.submit_command(device.device_id, "control.status")
        return bridge, session, command, follow_up, transport

    bridge, session, command, follow_up, transport = asyncio.run(scenario())
    assert bridge.get_session(session.session_id).state is SessionState.OFFLINE
    assert command.state is CommandState.OFFLINE
    assert command.error == {
        "code": "offline",
        "reason": "fake transport injected send failure",
    }
    assert follow_up.state is CommandState.OFFLINE
    assert follow_up.error["reason"] == "no_online_session"
    assert transport.connected is False


def test_session_disconnect_before_writer_acquisition_cannot_send_old_command():
    async def scenario():
        bridge, transport, device = _connected_bridge(auto_ack=False)
        session = await bridge.connect(device.device_id, transport)
        lock = bridge._lock_for(device.device_id)
        await lock.acquire()
        try:
            task = asyncio.create_task(bridge.submit_command(device.device_id, "control.status"))
            await asyncio.sleep(0)
            await bridge.disconnect(session.session_id)
        finally:
            lock.release()
        command = await task
        return command, transport

    command, transport = asyncio.run(scenario())
    assert command.state is CommandState.OFFLINE
    assert command.error["reason"] in {
        "transport_disconnected",
        "session_changed_before_dispatch",
    }
    assert not [frame for frame in transport.sent_frames if frame.get("kind") == "command"]


def test_expired_manual_lease_is_fenced_before_serial_write():
    async def scenario():
        capabilities = {"status", "manual_control_v1"}
        bridge, transport, device = _connected_bridge(capabilities=capabilities, auto_ack=False)
        session = await bridge.connect(device.device_id, transport)
        lease = bridge.acquire_control_lease(device.device_id, "ws-1")
        lock = bridge._lock_for(device.device_id)
        await lock.acquire()
        try:
            task = asyncio.create_task(
                bridge.submit_control_input(
                    lease.lease_id,
                    "ws-1",
                    input_seq=1,
                    action="input",
                    direction={"yaw": 1, "pitch": 0},
                )
            )
            await asyncio.sleep(0)
            bridge.reap_control_leases(now=lease.expires_at + timedelta(milliseconds=1))
        finally:
            lock.release()
        command = await task
        return bridge, session, lease, command, transport

    bridge, session, lease, command, transport = asyncio.run(scenario())
    assert bridge.get_control_lease(lease.lease_id).state is LeaseState.EXPIRED
    assert bridge.get_session(session.session_id).state is SessionState.ONLINE
    assert command.state is CommandState.EXPIRED
    assert command.error["reason"] == "manual_lease_no_longer_current"
    assert not [frame for frame in transport.sent_frames if frame.get("kind") == "command"]
