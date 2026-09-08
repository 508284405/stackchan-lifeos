"""W1 Web Bridge acceptance tests: fake device, persistence, and failure paths."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from bridge import Bridge, FakeTransport, SQLiteStore
from bridge.domain import BatchState, BatchTarget, BatchTargetState, BatchTask, CommandState, SessionState
from bridge.errors import ValidationError


def run(coro):
    return asyncio.run(coro)


def setup_bridge(store: SQLiteStore | None = None, **transport_kwargs):
    bridge = Bridge(store)
    transport = FakeTransport(**transport_kwargs)
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    return bridge, transport, device


def test_fake_device_completes_discover_claim_hello_command_and_audit():
    async def scenario():
        bridge, transport, device = setup_bridge()
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(
            device.device_id,
            "control.home",
            idempotency_key="web-home-1",
        )
        return bridge, transport, device, session, command

    bridge, transport, device, session, command = run(scenario())

    assert session.state is SessionState.ONLINE
    assert command.state is CommandState.COMPLETED
    assert command.wire_type == "command.control"
    assert command.payload["action"] == "home"
    assert transport.execution_count == 1
    wire_commands = [item for item in transport.sent_frames if item.get("kind") == "command"]
    assert len(wire_commands) == 1
    assert wire_commands[0]["event_id"] == command.command_id
    assert wire_commands[0]["device_id"] == device.device_id
    assert wire_commands[0]["seq"] == 1
    assert {item.kind.value for item in bridge.store.list_audits(device_id=device.device_id)} >= {
        "device.discovered",
        "device.claimed",
        "session.changed",
        "command.state.changed",
    }


def test_host_first_hello_matches_the_current_esp_idf_gateway_order():
    async def scenario():
        bridge, transport, device = setup_bridge(hello_mode="host_first")
        session = await bridge.connect(device.device_id, transport)
        return session, transport

    session, transport = run(scenario())
    assert session.state is SessionState.ONLINE
    assert [frame["type"] for frame in transport.sent_frames[:2]] == [
        "hello.host",
    ]


def test_host_first_hello_retries_the_same_envelope_after_a_boot_race():
    class DropsFirstHostHello(FakeTransport):
        def __init__(self):
            super().__init__(hello_mode="host_first")
            self.drop_first_host_hello = True

        async def send(self, envelope):
            if (
                self.drop_first_host_hello
                and envelope.get("kind") == "hello"
                and envelope.get("type") == "hello.host"
            ):
                self.drop_first_host_hello = False
                self.sent_frames.append(dict(envelope))
                return
            await super().send(envelope)

    async def scenario():
        bridge = Bridge()
        transport = DropsFirstHostHello()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        first = await bridge.connect(device.device_id, transport)
        assert first.state is SessionState.NEGOTIATING
        assert await bridge.retry_host_hello(device.device_id)
        return bridge.active_session_for_device(device.device_id), transport

    session, transport = run(scenario())
    hellos = [frame for frame in transport.sent_frames if frame.get("type") == "hello.host"]
    assert session is not None and session.state is SessionState.ONLINE
    assert len(hellos) == 2
    assert hellos[0]["event_id"] == hellos[1]["event_id"]
    assert hellos[0]["seq"] == hellos[1]["seq"]


def test_idempotency_returns_same_record_and_never_reexecutes():
    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        first = await bridge.submit_command(
            device.device_id,
            "control.status",
            idempotency_key="same-request",
        )
        second = await bridge.submit_command(
            device.device_id,
            "control.status",
            idempotency_key="same-request",
        )
        return first, second, transport

    first, second, transport = run(scenario())
    assert first.command_id == second.command_id
    assert transport.execution_count == 1
    assert len([item for item in transport.sent_frames if item.get("kind") == "command"]) == 1


def test_repeated_firmware_ack_event_id_is_scoped_by_command_correlation():
    async def scenario():
        bridge, transport, device = setup_bridge(auto_ack=False, auto_complete=False)
        session = await bridge.connect(device.device_id, transport)
        first = await bridge.submit_command(device.device_id, "control.status")
        second = await bridge.submit_command(device.device_id, "control.status")
        for command in (first, second):
            current = bridge.get_session(session.session_id).rx_seq
            await bridge.receive(
                session.session_id,
                {
                    "schema": "lifeos.v1",
                    "kind": "ack",
                    "type": "ack.command",
                    "event_id": "ack-0",
                    "correlation_id": command.command_id,
                    "device_id": device.device_id,
                    "seq": (current if current is not None else -1) + 1,
                    "ts_ms": 1,
                    "payload": {"status": "completed", "idempotent": True},
                },
            )
        return bridge.get_command(first.command_id), bridge.get_command(second.command_id)

    first, second = run(scenario())
    assert first.state is CommandState.COMPLETED
    assert second.state is CommandState.COMPLETED


def test_read_only_status_probe_omits_unsynchronised_device_ttl_fields():
    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status")
        return command, transport

    command, transport = run(scenario())
    assert command.state is CommandState.COMPLETED
    status_frames = [
        frame
        for frame in transport.sent_frames
        if frame.get("kind") == "command" and frame.get("type") == "command.control"
    ]
    assert status_frames[-1]["payload"] == {"action": "status"}


def test_status_ack_preserves_bounded_health_for_real_usb_monitoring():
    async def scenario():
        bridge, transport, device = setup_bridge(auto_ack=False)
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status")
        accepted = await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "ack",
                "type": "ack.command",
                "event_id": "ack-health-1",
                "correlation_id": command.command_id,
                "device_id": device.device_id,
                "seq": session.rx_seq + 1,
                "ts_ms": 2,
                "payload": {
                    "status": "completed",
                    "idempotent": True,
                    "firmware": "lifeos-phase1-0.3.0",
                    "torque_enabled": False,
                    "fault": False,
                    "uptime_ms": 123,
                    "nonce": "must-not-be-copied",
                },
            },
        )
        return accepted, bridge.get_command(command.command_id), bridge.latest_health_for_device(device.device_id)

    accepted, command, health = run(scenario())
    assert accepted is True
    assert command.state is CommandState.COMPLETED
    assert health == {
        "firmware": "lifeos-phase1-0.3.0",
        "torque_enabled": False,
        "fault": False,
        "uptime_ms": 123,
    }
    assert "nonce" not in command.result


def test_mapper_rejects_raw_wire_and_client_priority_is_not_an_input():
    with pytest.raises(ValidationError):
        Bridge.map_web_command(
            "command.control",
            {"action": "home"},
            issued_at_ms=1,
            expires_at_ms=2,
        )

    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        return await bridge.submit_command(
            device.device_id,
            "control.home",
            params={"reason": "test", "priority": 100},
        )

    command = run(scenario())
    assert command.state is CommandState.REJECTED
    assert command.error and command.error["code"] == "validation_error"


def test_preflight_maps_to_the_allowlisted_zero_motion_control_action():
    mapped = Bridge.map_web_command(
        "control.preflight",
        {},
        issued_at_ms=1,
        expires_at_ms=1_501,
    )
    assert mapped.wire_type == "command.control"
    assert mapped.payload == {"action": "preflight"}
    assert mapped.required_capability == "manual_preflight_v1"
    assert mapped.required_feature == "manual_control_v1"


def test_preflight_ack_is_terminal_but_does_not_claim_fresh_feedback():
    async def scenario():
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(
            capabilities={"status", "motion", "manual_control_v1", "manual_preflight_v1"},
            auto_ack=False,
        )
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.preflight")
        await transport.acknowledge_pending(command.command_id, status="accepted")
        return bridge.get_command(command.command_id), transport

    command, transport = run(scenario())
    assert command.state is CommandState.COMPLETED
    assert command.result == {"status": "accepted", "idempotent": False}
    preflight = [
        frame for frame in transport.sent_frames
        if frame.get("type") == "command.control" and frame.get("payload", {}).get("action") == "preflight"
    ]
    assert len(preflight) == 1


def test_resume_ack_is_terminal_after_the_firmware_synchronously_clears_pause():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport(capabilities={"status", "motion"}, auto_ack=False)
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.resume")
        await transport.acknowledge_pending(command.command_id, status="accepted")
        return bridge.get_command(command.command_id)

    command = run(scenario())
    assert command.state is CommandState.COMPLETED
    assert command.result == {"status": "accepted", "idempotent": False}


def test_preflight_rejects_a_device_without_the_versioned_capability():
    async def scenario():
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(capabilities={"status", "motion", "manual_control_v1"})
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        return await bridge.submit_command(device.device_id, "control.preflight")

    command = run(scenario())
    assert command.state is CommandState.REJECTED
    assert command.error == {
        "code": "capability_unavailable",
        "reason": "device_not_declared",
        "required_capability": "manual_preflight_v1",
    }


def test_command_params_reject_non_finite_or_oversized_values_before_persistence():
    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        with pytest.raises(ValidationError):
            await bridge.submit_command(device.device_id, "control.status", params={"value": math.nan})
        with pytest.raises(ValidationError):
            await bridge.submit_command(device.device_id, "control.status", params={"value": "x" * 9000})

    run(scenario())


def test_device_and_sequence_isolation_rejects_cross_talk_without_poisoning_peer():
    async def scenario():
        store = SQLiteStore()
        bridge = Bridge(store)
        first_transport = FakeTransport(device_id="fake-1", hardware_id="hw-1", transport_id="usb-1")
        second_transport = FakeTransport(device_id="fake-2", hardware_id="hw-2", transport_id="usb-2")
        for transport in (first_transport, second_transport):
            bridge.discover(transport.candidate())
            bridge.claim(transport.candidate().candidate_id)
        first_session = await bridge.connect("fake-1", first_transport)
        second_session = await bridge.connect("fake-2", second_transport)
        cross_talk = await bridge.receive(
            first_session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "health.report",
                "event_id": "cross-device",
                "device_id": "fake-2",
                "seq": 1,
                "ts_ms": 1,
                "payload": {},
            },
        )
        gap = await bridge.receive(
            first_session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "health.report",
                "event_id": "gap",
                "device_id": "fake-1",
                "seq": 9,
                "ts_ms": 9,
                "payload": {},
            },
        )
        good_heartbeat = await first_transport.emit_heartbeat()
        second_command = await bridge.submit_command("fake-2", "control.status")
        return bridge, first_session, second_session, cross_talk, gap, good_heartbeat, second_command

    bridge, first_session, second_session, cross_talk, gap, _, second_command = run(scenario())
    assert cross_talk is False
    assert gap is False
    assert bridge.get_session(first_session.session_id).state is SessionState.ONLINE
    assert bridge.get_session(second_session.session_id).state is SessionState.ONLINE
    assert second_command.state is CommandState.COMPLETED


def test_transport_failure_isolated_to_one_device():
    async def scenario():
        store = SQLiteStore()
        bridge = Bridge(store)
        broken = FakeTransport(device_id="broken", hardware_id="broken-hw", transport_id="usb-bad")
        healthy = FakeTransport(device_id="healthy", hardware_id="healthy-hw", transport_id="usb-good")
        for transport in (broken, healthy):
            bridge.discover(transport.candidate())
            bridge.claim(transport.candidate().candidate_id)
        await bridge.connect("broken", broken)
        await bridge.connect("healthy", healthy)
        broken.fail_send = True
        bad, good = await asyncio.gather(
            bridge.submit_command("broken", "control.status"),
            bridge.submit_command("healthy", "control.status"),
        )
        return bad, good

    bad, good = run(scenario())
    assert bad.state is CommandState.OFFLINE
    assert good.state is CommandState.COMPLETED


def test_ttl_terminal_state_is_not_reactivated_by_late_ack():
    async def scenario():
        bridge, transport, device = setup_bridge(auto_complete=False)
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status", ttl_ms=10)
        expired = bridge.expire_due(now_ms=command.expires_at_ms)
        await transport.acknowledge_pending(command.command_id, status="accepted")
        final = bridge.get_command(command.command_id)
        return session, expired, final

    _, expired, final = run(scenario())
    assert expired[0].state is CommandState.TIMEOUT
    assert final.state is CommandState.TIMEOUT
    assert final.late_evidence == [{"idempotent": False, "status": "accepted"}]


def test_rejected_ack_maps_to_safety_blocked_without_execution():
    async def scenario():
        bridge, transport, device = setup_bridge()
        transport.reject_commands["command.control"] = "fault_latched"
        await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status")
        return command, transport

    command, transport = run(scenario())
    assert command.state is CommandState.SAFETY_BLOCKED
    assert command.error == {"code": "fault_latched"}
    assert transport.execution_count == 0


def test_invalid_or_oversized_incoming_frames_are_rejected_and_audited():
    async def scenario():
        bridge, transport, device = setup_bridge()
        session = await bridge.connect(device.device_id, transport)
        invalid = await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "health.report",
                "event_id": "invalid-extra",
                "device_id": device.device_id,
                "seq": 1,
                "ts_ms": 1,
                "payload": {},
                "unexpected": True,
            },
        )
        oversized = await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "health.report",
                "event_id": "oversized",
                "device_id": device.device_id,
                "seq": 1,
                "ts_ms": 1,
                "payload": {"data": "x" * (16 * 1024)},
            },
        )
        return bridge, invalid, oversized

    bridge, invalid, oversized = run(scenario())
    assert invalid is False
    assert oversized is False
    assert sum(item.kind.value == "protocol.rejected" for item in bridge.store.list_audits()) >= 2


def test_restart_expires_nonterminal_commands_and_does_not_restore_session():
    async def scenario():
        store = SQLiteStore()
        bridge, transport, device = setup_bridge(store, auto_complete=False)
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status")
        restarted = Bridge(store)
        return session, command, restarted

    session, command, restarted = run(scenario())
    assert restarted.get_session(session.session_id).state is SessionState.OFFLINE
    assert restarted.get_command(command.command_id).state is CommandState.EXPIRED
    assert any(
        item.kind.value == "recovery.expired" and item.command_id == command.command_id
        for item in restarted.store.list_audits()
    )


def test_persistence_reopens_registered_device_and_audit_history(tmp_path):
    path = str(tmp_path / "bridge.sqlite3")

    async def scenario():
        store = SQLiteStore(path)
        bridge, transport, device = setup_bridge(store)
        await bridge.connect(device.device_id, transport)
        await bridge.submit_command(device.device_id, "control.status")
        store.close()

    run(scenario())
    with sqlite3.connect(path) as connection:
        session_blob = connection.execute("SELECT data FROM sessions LIMIT 1").fetchone()[0]
    assert "nonce" not in json.loads(session_blob)
    reopened = SQLiteStore(path)
    assert reopened.get_device("stackchan-fake-01") is not None
    assert len(reopened.list_audits(device_id="stackchan-fake-01")) >= 5
    reopened.close()


def test_stale_session_degrades_then_goes_offline_and_invalidates_commands():
    async def scenario():
        base = datetime(2026, 8, 30, tzinfo=timezone.utc)
        current = [base]
        bridge = Bridge(now_factory=lambda: current[0])
        transport = FakeTransport()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        session = await bridge.connect(device.device_id, transport)
        current[0] = base + timedelta(milliseconds=1600)
        degraded = bridge.check_freshness(now=current[0])
        degraded_state = degraded[0].state
        current[0] = base + timedelta(milliseconds=3201)
        offline = bridge.check_freshness(now=current[0])
        return session, degraded_state, offline[0].state

    _, degraded_state, offline_state = run(scenario())
    assert degraded_state is SessionState.DEGRADED
    assert offline_state is SessionState.OFFLINE


def test_single_target_batch_uses_command_service_and_persists_target_result():
    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        task = await bridge.submit_batch([device.device_id], "control.status")
        return bridge, task

    bridge, task = run(scenario())
    assert task.aggregate_state is BatchState.COMPLETED
    assert task.targets[0].state is BatchTargetState.COMPLETED
    assert task.targets[0].command_id is not None
    assert bridge.get_batch(task.task_id).targets[0].state is BatchTargetState.COMPLETED


def test_batch_reports_partial_without_hiding_an_offline_target():
    async def scenario():
        bridge, transport, device = setup_bridge()
        await bridge.connect(device.device_id, transport)
        task = await bridge.submit_batch([device.device_id, "missing-device"], "control.status")
        return task

    task = run(scenario())
    assert task.aggregate_state is BatchState.PARTIAL
    assert [target.state for target in task.targets] == [
        BatchTargetState.COMPLETED,
        BatchTargetState.OFFLINE,
    ]


def test_batch_cancel_only_marks_targets_that_have_not_entered_dispatch():
    bridge = Bridge()
    task = BatchTask(
        task_id="task-pending",
        command_type="control.status",
        params={},
        targets=[BatchTarget(device_id="not-yet-dispatched")],
        aggregate_state=BatchState.RUNNING,
    )
    bridge.store.save_batch(task)
    cancelled = bridge.cancel_batch(task.task_id)
    assert cancelled.aggregate_state is BatchState.CANCELLED
    assert cancelled.targets[0].state is BatchTargetState.CANCELLED
