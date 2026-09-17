import asyncio
from datetime import datetime, timezone
import pytest
from bridge import Bridge, FakeTransport, SQLiteStore
from bridge.errors import CapabilityUnavailable, ConflictError, ValidationError
from bridge.domain import MaintenanceTaskState

def setup(capabilities=None):
    bridge = Bridge(feature_gates={"maintenance": True})
    transport = FakeTransport(capabilities=capabilities or {"status", "maintenance_confirmation"})
    bridge.discover(transport.candidate()); device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    return bridge, transport, device

def test_default_gate_rejects_without_wire():
    bridge, transport, device = setup()
    bridge.feature_gates["maintenance"] = False
    with pytest.raises(CapabilityUnavailable):
        asyncio.run(bridge.prepare_maintenance(device.device_id, "factory_reset"))
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)

def test_confirmation_is_session_bound_and_one_shot():
    bridge, transport, device = setup()
    task = asyncio.run(bridge.prepare_maintenance(device.device_id, "factory_reset"))
    session = bridge.active_session_for_device(device.device_id)
    asyncio.run(
        transport.emit(
            kind="event",
            type="maintenance.confirmed",
            payload={
                "challenge_id": task.challenge_id,
                "operation": "factory_reset",
                "result": "confirmed",
                "valid_for_ms": 1_000,
            },
        )
    )
    assert bridge.maintenance.get(task.task_id).state is MaintenanceTaskState.CONFIRMED
    with pytest.raises(ConflictError):
        bridge.confirm_maintenance(
            task.challenge_id,
            device_id=device.device_id,
            session_id=session.session_id,
            operation="factory_reset",
            result="confirmed",
            valid_for_ms=1_000,
        )
    assert any(
        audit.device_id == device.device_id
        and audit.kind.value == "maintenance.challenge.changed"
        for audit in bridge.store.list_audits(device_id=device.device_id)
    )


def test_confirmation_requires_matching_operation_result_and_validity_window():
    bridge, transport, device = setup()
    task = asyncio.run(bridge.prepare_maintenance(device.device_id, "factory_reset", ttl_ms=1_000))
    session = bridge.active_session_for_device(device.device_id)
    for payload in (
        {
            "challenge_id": task.challenge_id,
            "operation": "wrong",
            "result": "confirmed",
            "valid_for_ms": 100,
        },
        {
            "challenge_id": task.challenge_id,
            "operation": "factory_reset",
            "result": "rejected",
            "valid_for_ms": 100,
        },
        {
            "challenge_id": task.challenge_id,
            "operation": "factory_reset",
            "result": "confirmed",
            "valid_for_ms": 2_000,
        },
    ):
        with pytest.raises((ConflictError, ValidationError)):
            bridge.confirm_maintenance(
                payload["challenge_id"],
                device_id=device.device_id,
                session_id=session.session_id,
                operation=payload["operation"],
                result=payload["result"],
                valid_for_ms=payload["valid_for_ms"],
            )
    assert bridge.maintenance.get(task.task_id).state is MaintenanceTaskState.AWAITING_CONFIRMATION


def test_session_disconnect_expires_confirmation_and_prevents_execute():
    bridge, transport, device = setup()
    task = asyncio.run(bridge.prepare_maintenance(device.device_id, "factory_reset"))
    session = bridge.active_session_for_device(device.device_id)
    asyncio.run(bridge.disconnect(session.session_id))
    assert bridge.maintenance.get(task.task_id).state is MaintenanceTaskState.EXPIRED
    with pytest.raises(ConflictError):
        asyncio.run(bridge.execute_maintenance(task.task_id))

def test_confirmed_factory_reset_executes_only_through_the_maintenance_wire_path():
    bridge, transport, device = setup({"status", "maintenance_confirmation", "maintenance_execute"})
    task = asyncio.run(bridge.prepare_maintenance(device.device_id, "factory_reset"))
    session = bridge.active_session_for_device(device.device_id)
    bridge.confirm_maintenance(
        task.challenge_id,
        device_id=device.device_id,
        session_id=session.session_id,
        operation="factory_reset",
        result="confirmed",
        valid_for_ms=1_000,
    )
    result = asyncio.run(bridge.execute_maintenance(task.task_id))
    assert result.state is MaintenanceTaskState.COMPLETED
    actions = [
        frame.get("payload", {}).get("action")
        for frame in transport.sent_frames
        if frame.get("type") == "command.maintenance"
    ]
    assert actions == ["prepare", "execute"]


def test_confirmed_operation_cannot_execute_after_confirmation_expiry():
    current = [datetime(2026, 9, 17, tzinfo=timezone.utc)]
    bridge = Bridge(
        feature_gates={"maintenance": True},
        now_factory=lambda: current[0],
    )
    transport = FakeTransport(
        capabilities={"status", "maintenance_confirmation", "maintenance_execute"}
    )
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    task = asyncio.run(
        bridge.prepare_maintenance(device.device_id, "factory_reset", ttl_ms=1_000)
    )
    session = bridge.active_session_for_device(device.device_id)
    bridge.confirm_maintenance(
        task.challenge_id,
        device_id=device.device_id,
        session_id=session.session_id,
        operation="factory_reset",
        result="confirmed",
        valid_for_ms=100,
    )
    current[0] = current[0].replace(microsecond=200_000)

    with pytest.raises(ConflictError, match="expired"):
        asyncio.run(bridge.execute_maintenance(task.task_id))

    assert bridge.maintenance.get(task.task_id).state is MaintenanceTaskState.EXPIRED

def test_maintenance_task_survives_restart_and_inflight_expires(tmp_path):
    store = SQLiteStore(str(tmp_path / "bridge.db"))
    bridge = Bridge(store, feature_gates={"maintenance": True})
    task = bridge.maintenance
    # Persist a task through the public manager without device I/O.
    from bridge.domain import MaintenanceTask
    record = MaintenanceTask("mt-persist", "dev", "restart", "session", "challenge")
    task._save(record)
    restarted = Bridge(store)
    assert restarted.maintenance.get("mt-persist").state is MaintenanceTaskState.EXPIRED
