"""W3.1 dedicated remote emergency-stop path tests."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from bridge import Bridge, FakeTransport
from bridge.api import create_app
from bridge.domain import CommandState, LeaseState
from bridge.errors import ConflictError


def test_emergency_stop_preempts_admitted_ordinary_command_and_completes():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport(auto_complete=False)
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        ordinary = await bridge.submit_command(device.device_id, "control.status")
        transport.auto_complete = True
        emergency = await bridge.submit_emergency_stop(
            device.device_id,
            reason="operator requested",
            idempotency_key="estop-1",
        )
        retry = await bridge.submit_emergency_stop(
            device.device_id,
            reason="operator requested",
            idempotency_key="estop-1",
        )
        return bridge, ordinary.command_id, emergency, retry, transport

    bridge, ordinary_id, emergency, retry, transport = asyncio.run(scenario())
    ordinary = bridge.get_command(ordinary_id)
    emergency = bridge.get_command(emergency.command_id)
    assert ordinary.state is CommandState.PREEMPTED
    assert ordinary.error == {"code": "preempted", "reason": "emergency_stop"}
    assert emergency.state is CommandState.COMPLETED
    assert retry.command_id == emergency.command_id
    assert transport.execution_count == 2
    frames = [frame for frame in transport.sent_frames if frame.get("kind") == "command"]
    assert frames[-1]["type"] == "command.emergency_stop"
    assert frames[-1]["payload"] == {"reason": "operator requested"}


def test_emergency_stop_offline_is_explicitly_not_delivered():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        return await bridge.submit_emergency_stop(device.device_id, reason="offline")

    command = asyncio.run(scenario())
    assert command.state is CommandState.OFFLINE
    assert command.error == {"code": "offline", "reason": "no_online_session", "not_delivered": True}


def test_emergency_preemption_is_checked_before_ordinary_command_dispatch():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        device_lock = bridge._lock_for(device.device_id)
        await device_lock.acquire()
        ordinary_task = asyncio.create_task(bridge.submit_command(device.device_id, "control.status"))
        await asyncio.sleep(0)
        emergency_task = asyncio.create_task(
            bridge.submit_emergency_stop(device.device_id, reason="preempt")
        )
        await asyncio.sleep(0)
        device_lock.release()
        emergency = await emergency_task
        ordinary = await ordinary_task
        return bridge, ordinary, emergency, transport

    bridge, ordinary, emergency, transport = asyncio.run(scenario())
    assert ordinary.state is CommandState.PREEMPTED
    assert bridge.get_command(ordinary.command_id).state is CommandState.PREEMPTED
    assert emergency.state is CommandState.COMPLETED
    assert [frame["type"] for frame in transport.sent_frames if frame.get("kind") == "command"] == [
        "command.emergency_stop"
    ]


def test_emergency_stop_preempts_an_active_manual_lease():
    async def scenario():
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(capabilities={"manual_control_v1", "safety"})
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        lease = bridge.acquire_control_lease(device.device_id, "ws-1")
        emergency = await bridge.submit_emergency_stop(device.device_id, reason="safety")
        return bridge, lease, emergency

    bridge, lease, emergency = asyncio.run(scenario())
    assert emergency.state is CommandState.COMPLETED
    assert bridge.get_control_lease(lease.lease_id).state is LeaseState.PREEMPTED
    assert bridge.get_session(lease.session_id).active_control_lease is None


def test_emergency_stop_api_is_separate_and_never_accepts_client_priority():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        response = client.post(
            f"/api/v1/devices/{device.device_id}/emergency-stop",
            json={"reason": "api test", "idempotency_key": "api-estop"},
        )
        invalid = client.post(
            f"/api/v1/devices/{device.device_id}/emergency-stop",
            json={"priority": 1},
        )

    assert response.status_code == 202
    assert response.json()["type"] == "emergency_stop"
    assert response.json()["state"] == "completed"
    assert invalid.status_code == 422
    assert not any("priority" in frame.get("payload", {}) for frame in transport.sent_frames)
