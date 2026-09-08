"""W5 Edge envelope and host-only deterministic fake contract tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bridge.edge import EDGE_SCHEMA, EdgeSession, EdgeState, FakeEdgeTransport, validate_edge_envelope
from bridge.errors import TransportError, ValidationError


ROOT = Path(__file__).parents[2]


def test_schema_fixtures_have_positive_and_negative_examples():
    valid = json.loads((ROOT / "contracts/edge/examples/command.valid.json").read_text())
    invalid = json.loads((ROOT / "contracts/edge/examples/invalid.raw-wire.json").read_text())
    validate_edge_envelope(valid)
    with pytest.raises(ValidationError):
        validate_edge_envelope({**invalid, "payload": {"raw": "x"}, "nonce": "not-allowed-here"})


def test_edge_contract_rejects_lifeos_wire_and_unknown_fields():
    valid = json.loads((ROOT / "contracts/edge/examples/command.valid.json").read_text())
    with pytest.raises(ValidationError):
        validate_edge_envelope({**valid, "schema": "lifeos.v1"})
    with pytest.raises(ValidationError):
        validate_edge_envelope({**valid, "browser_dto": True})


def test_action_boundary_rejects_raw_wire_and_hardware_fields():
    async def scenario():
        transport = FakeEdgeTransport()
        session = EdgeSession(transport)
        await session.connect()
        with pytest.raises(ValidationError):
            await session.send_remote_action("command.control", now_ms=1)
        with pytest.raises(ValidationError):
            await session.send_remote_action("home", now_ms=1, params={"yaw_deg": 10})
        with pytest.raises(ValidationError):
            await session.send_remote_action("home", now_ms=1, params={"nested": {"pwm": 10}})

    asyncio.run(scenario())


def test_session_uses_independent_zero_based_sequences_and_binding():
    async def scenario():
        transport = FakeEdgeTransport(auto_hello=True)
        session = EdgeSession(transport)
        await session.connect(now_ms=100)
        command = await session.send_remote_action("home", now_ms=101)
        first_session = session.session_id
        await session.reconnect(now_ms=200)
        second_session = session.session_id
        return session, transport, command, first_session, second_session

    session, transport, command, first, second = asyncio.run(scenario())
    assert session.state is EdgeState.ONLINE
    assert first != second
    assert command.session_id == first
    assert [frame["seq"] for frame in transport.sent_frames] == [0, 1, 0]
    assert transport.sent_frames[0]["schema"] == EDGE_SCHEMA
    assert transport.sent_frames[0]["sender"]["kind"] == "control_plane"


def test_disconnect_rejects_new_action_and_reconnect_does_not_replay_old_command():
    async def scenario():
        transport = FakeEdgeTransport()
        session = EdgeSession(transport)
        await session.connect()
        old = await session.send_remote_action("status", now_ms=1)
        await session.disconnect()
        with pytest.raises(TransportError):
            await session.send_remote_action("home", now_ms=2)
        await session.reconnect(now_ms=3)
        return old, session, transport

    old, session, transport = asyncio.run(scenario())
    assert old.command_id in session.commands
    assert len([f for f in transport.sent_frames if f["kind"] == "command"]) == 1
    assert all(f["session_id"] != session.session_id for f in transport.sent_frames if f["kind"] == "command")


def test_duplicate_out_of_order_expired_and_backpressure_faults_are_observable():
    async def scenario():
        transport = FakeEdgeTransport()
        session = EdgeSession(transport)
        await session.connect()
        transport.inject_duplicate()
        await transport.emit({"schema": EDGE_SCHEMA, "kind": "heartbeat", "type": "heartbeat.edge", "message_id": "h1", "sender": {"kind": "edge", "id": transport.edge_id}, "edge_id": transport.edge_id, "device_id": transport.device_id, "session_id": session.session_id, "seq": 1, "ts_ms": 10, "payload": {}})
        transport.inject_reorder()
        frame = {"schema": EDGE_SCHEMA, "kind": "heartbeat", "type": "heartbeat.edge", "message_id": "h2", "sender": {"kind": "edge", "id": transport.edge_id}, "edge_id": transport.edge_id, "device_id": transport.device_id, "session_id": session.session_id, "seq": 2, "ts_ms": 20, "payload": {}}
        await transport.emit(frame)
        await transport.emit({**frame, "message_id": "h3", "seq": 3, "ts_ms": 21})
        transport.inject_expired()
        command = await session.send_remote_action("status", now_ms=30)
        transport.backpressured = True
        with pytest.raises(TransportError):
            await session.send_remote_action("home", now_ms=31)
        return session, transport, command

    session, transport, command = asyncio.run(scenario())
    assert any(
        event["type"] in {"edge.sequence_rejected", "edge.message_rejected"}
        for event in session.events
    )
    assert transport.sent_frames[-1]["command_id"] == command.command_id
    assert transport.sent_frames[-1]["expires_at_ms"] < transport.sent_frames[-1]["ts_ms"]


def test_heartbeat_degrades_then_expires_without_network_io():
    async def scenario():
        transport = FakeEdgeTransport()
        session = EdgeSession(transport)
        await session.connect(now_ms=0)
        return session.tick(now_ms=15_001), session.tick(now_ms=45_001)

    degraded, offline = asyncio.run(scenario())
    assert degraded is EdgeState.DEGRADED
    assert offline is EdgeState.OFFLINE
