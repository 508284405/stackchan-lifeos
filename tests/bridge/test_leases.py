"""W3 host-side lease and manual-control gate tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bridge import Bridge, FakeTransport
from bridge.domain import DeviceSession, LeaseState, SessionState
from bridge.errors import CapabilityUnavailable, ConflictError, NotFoundError, ValidationError
from bridge.leases import ControlLeaseManager


def test_lease_manager_enforces_binding_sequence_ttl_and_release():
    base = datetime(2026, 8, 30, tzinfo=timezone.utc)
    manager = ControlLeaseManager(now_factory=lambda: base)
    session = DeviceSession(
        session_id="session-1",
        device_id="device-1",
        edge_id="local",
        transport_id="usb-1",
        nonce="runtime-only",
        state=SessionState.ONLINE,
        capabilities=frozenset({"manual_control_v1"}),
    )

    lease = manager.acquire(session, "ws-1", now=base)
    payload = manager.accept_input(
        lease.lease_id,
        device_id="device-1",
        session_id="session-1",
        connection_id="ws-1",
        input_seq=1,
        action="input",
        direction={"yaw": 0.5, "pitch": -0.25},
        ttl_ms=400,
        now=base,
    )
    assert payload["direction"] == {"yaw": 0.5, "pitch": -0.25}
    with pytest.raises(ConflictError):
        manager.accept_input(
            lease.lease_id,
            device_id="device-1",
            session_id="session-1",
            connection_id="other-ws",
            input_seq=2,
            action="input",
            direction={"yaw": 0, "pitch": 0},
            ttl_ms=400,
            now=base,
        )
    with pytest.raises(ConflictError):
        manager.accept_input(
            lease.lease_id,
            device_id="device-1",
            session_id="session-1",
            connection_id="ws-1",
            input_seq=3,
            action="input",
            direction={"yaw": 0, "pitch": 0},
            ttl_ms=400,
            now=base,
        )
    with pytest.raises(ConflictError):
        manager.accept_input(
            lease.lease_id,
            device_id="device-1",
            session_id="session-1",
            connection_id="ws-1",
            input_seq=2,
            action="input",
            direction={"yaw": 0, "pitch": 0},
            ttl_ms=400,
            now=base + timedelta(milliseconds=50),
        )
    manager.accept_input(
        lease.lease_id,
        device_id="device-1",
        session_id="session-1",
        connection_id="ws-1",
        input_seq=2,
        action="input",
        direction={"yaw": 0, "pitch": 0},
        ttl_ms=400,
        now=base + timedelta(milliseconds=100),
    )
    renewed = manager.renew(lease.lease_id, "ws-1", ttl_ms=500, now=base + timedelta(milliseconds=100))
    assert renewed.expires_at <= renewed.max_expires_at
    released = manager.release(lease.lease_id, "ws-1")
    assert released.state is LeaseState.RELEASED


def test_manual_inputs_slide_the_lease_for_continuous_hold():
    base = datetime(2026, 8, 30, tzinfo=timezone.utc)
    manager = ControlLeaseManager(now_factory=lambda: base)
    session = DeviceSession(
        session_id="session-1",
        device_id="device-1",
        edge_id="local",
        transport_id="usb-1",
        nonce="runtime-only",
        state=SessionState.ONLINE,
        capabilities=frozenset({"manual_control_v1"}),
    )
    lease = manager.acquire(session, "ws-1", now=base)
    manager.accept_input(
        lease.lease_id,
        device_id="device-1",
        session_id="session-1",
        connection_id="ws-1",
        input_seq=1,
        action="input",
        direction={"yaw": 0, "pitch": 0},
        ttl_ms=400,
        now=base,
    )
    assert lease.expires_at == base + timedelta(milliseconds=400)
    manager.accept_input(
        lease.lease_id,
        device_id="device-1",
        session_id="session-1",
        connection_id="ws-1",
        input_seq=2,
        action="input",
        direction={"yaw": 0, "pitch": 0},
        ttl_ms=400,
        now=base + timedelta(milliseconds=300),
    )
    assert lease.expires_at == base + timedelta(milliseconds=700)
    assert manager.expire(now=base + timedelta(milliseconds=500)) == []
    assert lease.state is LeaseState.ACTIVE


def test_bridge_keeps_manual_control_disabled_by_default():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        return bridge, device

    bridge, device = asyncio.run(scenario())
    with pytest.raises(CapabilityUnavailable) as error:
        bridge.acquire_control_lease(device.device_id, "ws-1")
    assert error.value.reason == "feature_gate_disabled"
    assert error.value.required_capability == "manual_control_v1"


def test_real_manual_control_requires_explicit_transport_verification():
    async def scenario(verified: bool):
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(
            capabilities={"status", "motion", "safety", "manual_control_v1"},
        )
        transport.host_test_only = False
        transport.manual_control_verified = verified
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        return bridge, device

    bridge, device = asyncio.run(scenario(False))
    with pytest.raises(CapabilityUnavailable) as error:
        bridge.acquire_control_lease(device.device_id, "ws-1")
    assert error.value.reason == "real_transport_not_verified"

    bridge, device = asyncio.run(scenario(True))
    lease = bridge.acquire_control_lease(device.device_id, "ws-1")
    assert lease.device_id == device.device_id


def test_host_hello_only_negotiates_manual_capability_when_gate_is_enabled():
    async def scenario(enabled: bool):
        bridge = Bridge(feature_gates={"manual_control_v1": enabled})
        transport = FakeTransport(capabilities={"status", "manual_control_v1"})
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        hello = next(frame for frame in transport.sent_frames if frame.get("type") == "hello.host")
        return hello["payload"]["capabilities"]

    disabled_capabilities = asyncio.run(scenario(False))
    enabled_capabilities = asyncio.run(scenario(True))
    assert "manual_control_v1" not in disabled_capabilities
    assert "manual_control_v1" in enabled_capabilities


def test_host_first_hello_bootstraps_registered_manual_capability():
    async def scenario():
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(
            capabilities={"status", "manual_control_v1"},
            hello_mode="host_first",
        )
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        session = await bridge.connect(device.device_id, transport)
        hello = next(frame for frame in transport.sent_frames if frame.get("type") == "hello.host")
        return session, hello

    session, hello = asyncio.run(scenario())
    assert session.state is SessionState.ONLINE
    assert "manual_control_v1" in hello["payload"]["capabilities"]


def test_enabled_fake_manual_control_uses_lease_and_versioned_payload():
    async def scenario():
        bridge = Bridge(feature_gates={"manual_control_v1": True})
        transport = FakeTransport(
            capabilities={"status", "motion", "safety", "manual_control_v1"},
            auto_complete=False,
        )
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        lease = bridge.acquire_control_lease(device.device_id, "ws-1")
        assert bridge.get_session(lease.session_id).active_control_lease == lease.lease_id
        first = await bridge.submit_control_input(
            lease.lease_id,
            "ws-1",
            input_seq=1,
            action="input",
            direction={"yaw": 0.75, "pitch": -0.2},
        )
        retry = await bridge.submit_control_input(
            lease.lease_id,
            "ws-1",
            input_seq=1,
            action="input",
            direction={"yaw": 0.75, "pitch": -0.2},
        )
        release = await bridge.submit_control_input(
            lease.lease_id,
            "ws-1",
            input_seq=2,
            action="release",
            direction=None,
        )
        return bridge, transport, lease, first, retry, release

    bridge, transport, lease, first, retry, release = asyncio.run(scenario())
    assert first.command_id == retry.command_id
    assert first.state.value == "completed"
    assert release.state.value == "completed"
    assert bridge.get_control_lease(lease.lease_id).state is LeaseState.RELEASED
    assert bridge.get_session(lease.session_id).active_control_lease is None
    manual_frames = [frame for frame in transport.sent_frames if frame.get("type") == "command.manual_control"]
    assert len(manual_frames) == 2
    assert manual_frames[0]["payload"] == {
        "lease_id": lease.lease_id,
        "input_seq": 1,
        "action": "input",
        "direction": {"yaw": 0.75, "pitch": -0.2},
        "ttl_ms": 400,
    }
    assert "yaw_deg" not in manual_frames[0]["payload"]
    assert transport.execution_count == 2
    assert any(item.kind.value == "control.lease.changed" for item in bridge.store.list_audits())


def test_expired_lease_rejects_old_input_and_bridge_restart_has_no_lease():
    async def scenario():
        base = datetime(2026, 8, 30, tzinfo=timezone.utc)
        current = [base]
        bridge = Bridge(
            now_factory=lambda: current[0],
            feature_gates={"manual_control_v1": True},
        )
        transport = FakeTransport(capabilities={"manual_control_v1"})
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        lease = bridge.acquire_control_lease(device.device_id, "ws-1", ttl_ms=300, max_duration_ms=500)
        current[0] = base + timedelta(milliseconds=301)
        expired = bridge.reap_control_leases()
        with pytest.raises(ConflictError):
            await bridge.submit_control_input(
                lease.lease_id,
                "ws-1",
                input_seq=1,
                action="input",
                direction={"yaw": 0, "pitch": 0},
            )
        restarted = Bridge(bridge.store, feature_gates={"manual_control_v1": True})
        return lease, expired, restarted

    lease, expired, restarted = asyncio.run(scenario())
    assert expired[0].state is LeaseState.EXPIRED
    with pytest.raises(NotFoundError):
        restarted.get_control_lease(lease.lease_id)


def test_manual_input_rejects_raw_or_out_of_range_values():
    base = datetime(2026, 8, 30, tzinfo=timezone.utc)
    manager = ControlLeaseManager(now_factory=lambda: base)
    session = DeviceSession(
        session_id="session-1",
        device_id="device-1",
        edge_id="local",
        transport_id="usb-1",
        nonce="runtime-only",
        state=SessionState.ONLINE,
        capabilities=frozenset({"manual_control_v1"}),
    )
    lease = manager.acquire(session, "ws-1", now=base)
    with pytest.raises(ValidationError):
        manager.accept_input(
            lease.lease_id,
            device_id="device-1",
            session_id="session-1",
            connection_id="ws-1",
            input_seq=1,
            action="input",
            direction={"yaw_deg": 10, "pitch": 0},
            ttl_ms=400,
            now=base,
        )
    with pytest.raises(ValidationError):
        manager.accept_input(
            lease.lease_id,
            device_id="device-1",
            session_id="session-1",
            connection_id="ws-1",
            input_seq=1,
            action="input",
            direction={"yaw": 2, "pitch": 0},
            ttl_ms=400,
            now=base,
        )
