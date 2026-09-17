"""W1 REST DTO and deployment-boundary checks."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from bridge import Bridge, FakeTransport
from bridge.api import create_app
from bridge.domain import LeaseState
from bridge.errors import CapabilityUnavailable


def test_api_exposes_device_and_command_lifecycle_without_raw_wire_route():
    store_bridge = Bridge()
    transport = FakeTransport()
    store_bridge.discover(transport.candidate())
    device = store_bridge.claim(transport.candidate().candidate_id)
    asyncio.run(store_bridge.connect(device.device_id, transport))

    with TestClient(create_app(store_bridge)) as client:
        health = client.get("/api/v1/health")
        devices = client.get("/api/v1/devices")
        detail = client.get(f"/api/v1/devices/{device.device_id}")
        web = client.get("/")
        command = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "control.status", "idempotency_key": "api-status"},
        )
        fetched = client.get(f"/api/v1/commands/{command.json()['command_id']}")
        raw = client.post(
            f"/api/v1/devices/{device.device_id}/envelope",
            json={"schema": "lifeos.v1"},
        )

    assert health.status_code == 200
    assert health.json()["bind_host"] == "127.0.0.1"
    assert health.json()["feature_gates"] == {
        "control": True,
        "manual_control_v1": False,
        "media": False,
    }
    assert devices.json()["items"][0]["session"]["state"] == "online"
    assert detail.status_code == 200
    assert detail.json()["device_id"] == device.device_id
    assert web.status_code == 200
    assert "LifeOS / Web Bridge" in web.text
    assert command.status_code == 202
    assert command.json()["state"] == "completed"
    assert fetched.status_code == 200
    assert raw.status_code in {404, 405}


def test_api_claim_requires_candidate_identity_to_match_path():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    with TestClient(create_app(bridge)) as client:
        mismatch = client.post(
            "/api/v1/devices/not-the-device/claim",
            json={"candidate_id": transport.candidate().candidate_id},
        )
        claimed = client.post(
            f"/api/v1/devices/{transport.device_id}/claim",
            json={"candidate_id": transport.candidate().candidate_id},
        )

    assert mismatch.status_code == 409
    assert claimed.status_code == 200
    assert claimed.json()["device_id"] == transport.device_id


def test_api_rejects_client_priority_and_unavailable_features():
    bridge = Bridge()
    transport = FakeTransport(capabilities={"status", "control"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    with TestClient(create_app(bridge)) as client:
        unknown = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "control.home", "params": {"priority": 100}},
        )
        manual = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "manual_control"},
        )

    assert unknown.status_code == 422
    assert manual.status_code == 409
    assert manual.json()["error"]["reason"] == "feature_gate_disabled"
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)


def test_generic_manual_control_endpoint_never_bypasses_the_control_lease():
    bridge = Bridge(feature_gates={"manual_control_v1": True})
    transport = FakeTransport(capabilities={"manual_control_v1", "status"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    with TestClient(create_app(bridge)) as client:
        response = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "manual_control"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["reason"] == "control_lease_required"
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)


def test_manual_control_requires_a_test_transport_until_real_wire_is_verified():
    bridge = Bridge(feature_gates={"manual_control_v1": True})
    transport = FakeTransport(capabilities={"manual_control_v1"})
    transport.host_test_only = False
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with pytest.raises(CapabilityUnavailable, match="real_transport_not_verified"):
        bridge.acquire_control_lease(device.device_id, "test-connection")


def test_w4_api_routes_keep_maintenance_and_rollout_execution_gated():
    bridge = Bridge(feature_gates={"maintenance": True})
    transport = FakeTransport(capabilities={"status", "maintenance_confirmation"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        prepared = client.post(
            f"/api/v1/devices/{device.device_id}/maintenance/prepare",
            json={"operation": "factory_reset"},
        )
        execute_before_confirmation = client.post(
            f"/api/v1/maintenance-tasks/{prepared.json()['task_id']}/execute"
        )
        diagnostics = client.get(f"/api/v1/devices/{device.device_id}/diagnostics")
        rollout = client.post(
            f"/api/v1/devices/{device.device_id}/rollout-tasks",
            json={"image_ref": "image-v1"},
        )
        invalid_preflight = client.post(
            f"/api/v1/rollout-tasks/{rollout.json()['task_id']}/preflight",
            json={"checks": {"unknown": True}},
        )

    assert prepared.status_code == 202
    assert execute_before_confirmation.status_code == 409
    assert diagnostics.status_code == 200
    assert "nonce" not in diagnostics.text
    assert rollout.status_code == 202
    assert invalid_preflight.status_code == 422
    actions = [
        frame.get("payload", {}).get("action")
        for frame in transport.sent_frames
        if frame.get("type") == "command.maintenance"
    ]
    assert actions == ["prepare"]


def test_api_requires_explicit_trusted_lan_opt_in_for_non_loopback():
    with pytest.raises(ValueError):
        create_app(bind_host="192.168.1.20")
    with pytest.raises(ValueError):
        create_app(bind_host="0.0.0.0", web_no_auth_trusted_lan=True)
    with pytest.raises(ValueError):
        create_app(bind_host="8.8.8.8", web_no_auth_trusted_lan=True)
    app = create_app(bind_host="192.168.1.20", web_no_auth_trusted_lan=True)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["web_no_auth_trusted_lan"] is True


def test_trusted_lan_high_impact_gates_require_explicit_origin_and_cors_is_allowlisted():
    with pytest.raises(ValueError, match="origin_allowlist"):
        create_app(
            Bridge(feature_gates={"manual_control_v1": True}),
            bind_host="192.168.1.20",
            web_no_auth_trusted_lan=True,
        )
    with pytest.raises(ValueError, match="non-wildcard"):
        create_app(origin_allowlist={"*"})

    app = create_app(
        Bridge(feature_gates={"manual_control_v1": True}),
        bind_host="192.168.1.20",
        web_no_auth_trusted_lan=True,
        origin_allowlist={"http://trusted.local"},
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"Origin": "http://trusted.local"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://trusted.local"
    assert response.json()["origin_allowlist_configured"] is True


def test_api_event_stream_filters_devices_and_delivers_domain_events():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    asyncio.run(bridge.submit_command(device.device_id, "control.status"))

    with TestClient(create_app(bridge)) as client:
        with client.websocket_connect("/api/v1/events") as websocket:
            websocket.send_json({"device_ids": [device.device_id], "cursor": 0})
            received = []
            for _ in range(20):
                event = websocket.receive_json()
                received.append(event)
                if event.get("type") == "command.state.changed" and event.get("payload", {}).get("to") == "completed":
                    break

    assert received
    assert all(event["device_id"] == device.device_id for event in received)
    assert any(event["type"] == "device.session.changed" for event in received)
    assert any(event["type"] == "command.state.changed" for event in received)
    assert all("nonce" not in event for event in received)


def test_api_can_enforce_an_explicit_websocket_origin_allowlist():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    app = create_app(bridge, origin_allowlist={"http://trusted.local"})

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/v1/events",
                headers={"origin": "http://untrusted.local"},
            ):
                pass
        with client.websocket_connect(
            "/api/v1/events",
            headers={"origin": "http://trusted.local"},
        ) as websocket:
            websocket.send_json({"device_ids": [device.device_id], "cursor": 0})
            assert websocket.receive_json()["type"] == "device.summary.changed"


def test_control_websocket_binds_lease_and_manual_input_to_server_connection():
    bridge = Bridge(feature_gates={"manual_control_v1": True})
    transport = FakeTransport(capabilities={"manual_control_v1", "status", "motion", "safety"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        with client.websocket_connect("/api/v1/control") as websocket:
            connected = websocket.receive_json()
            assert connected["type"] == "control.connected"
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            acquired = websocket.receive_json()
            assert acquired["type"] == "lease.acquired"
            lease = acquired["lease"]
            websocket.send_json(
                {
                    "type": "input",
                    "lease_id": lease["lease_id"],
                    "input_seq": 1,
                    "action": "input",
                    "direction": {"yaw": 0.25, "pitch": 0},
                }
            )
            command = websocket.receive_json()
            assert command["type"] == "command.state.changed"
            assert command["command"]["state"] == "completed"
            websocket.send_json({"type": "lease.release", "lease_id": lease["lease_id"]})
            released = websocket.receive_json()
            assert released["type"] == "lease.released"

    assert bridge.get_control_lease(lease["lease_id"]).state is LeaseState.RELEASED


def test_control_websocket_stops_camera_before_acquiring_a_manual_lease():
    bridge = Bridge(feature_gates={"manual_control_v1": True, "media": True})
    transport = FakeTransport(capabilities={"manual_control_v1", "status", "motion", "safety", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        started = client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        )
        assert started.status_code == 202
        assert bridge._active_camera_previews
        with client.websocket_connect("/api/v1/control") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            acquired = websocket.receive_json()

    assert acquired["type"] == "lease.acquired"
    assert acquired["camera_preview_stopped"] is True
    assert not bridge._active_camera_previews
    actions = [
        frame.get("payload", {}).get("action")
        for frame in transport.sent_frames
        if frame.get("type") == "command.camera_preview"
    ]
    assert actions == ["start", "stop"]


def test_real_manual_preview_gate_preserves_camera_during_lease_acquisition():
    bridge = Bridge(feature_gates={
        "manual_control_v1": True,
        "media": True,
        "manual_camera_preview": True,
    })
    transport = FakeTransport(capabilities={"manual_control_v1", "status", "motion", "safety", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        started = client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        )
        assert started.status_code == 202
        with client.websocket_connect("/api/v1/control") as websocket:
            connected = websocket.receive_json()
            connection_id = connected["connection_id"]
            websocket.send_json({"type": "viewer.open", "device_id": device.device_id})
            assert websocket.receive_json()["type"] == "viewer.opened"
            frame = client.get(
                f"/api/v1/devices/{device.device_id}/camera/frame",
                params={"viewer_id": connection_id},
            )
            assert frame.status_code == 200
            websocket.send_json({
                "type": "video.displayed",
                "device_id": device.device_id,
                "frame_id": frame.headers["X-LifeOS-Frame-Id"],
                "token": frame.headers["X-LifeOS-Frame-Token"],
                "visible": True,
            })
            assert websocket.receive_json()["type"] == "video.display.acknowledged"
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            acquired = websocket.receive_json()

    assert acquired["type"] == "lease.acquired"
    assert acquired["camera_preview_stopped"] is False
    assert bridge._active_camera_previews
    actions = [
        frame.get("payload", {}).get("action")
        for frame in transport.sent_frames
        if frame.get("type") == "command.camera_preview"
    ]
    assert actions == ["start"]


def test_manual_preview_rejects_a_displayed_frame_older_than_500ms():
    bridge = Bridge(feature_gates={
        "manual_control_v1": True,
        "media": True,
        "manual_camera_preview": True,
    })
    transport = FakeTransport(
        capabilities={"manual_control_v1", "status", "motion", "safety", "camera"}
    )
    transport.camera_capture_age_ms = 750
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    # Advance the device clock without a new clock sample so the captured
    # frame cannot be proven to be within the 500 ms control bound.
    transport._device_clock_ms = 1_000

    with TestClient(create_app(bridge)) as client:
        assert client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        ).status_code == 202
        with client.websocket_connect("/api/v1/control") as websocket:
            connection_id = websocket.receive_json()["connection_id"]
            websocket.send_json({"type": "viewer.open", "device_id": device.device_id})
            assert websocket.receive_json()["type"] == "viewer.opened"
            frame = client.get(
                f"/api/v1/devices/{device.device_id}/camera/frame",
                params={"viewer_id": connection_id},
            )
            websocket.send_json({
                "type": "video.displayed",
                "device_id": device.device_id,
                "frame_id": frame.headers["X-LifeOS-Frame-Id"],
                "token": frame.headers["X-LifeOS-Frame-Token"],
                "visible": True,
            })
            rejected = websocket.receive_json()

    assert rejected["type"] == "control.error"
    assert rejected["code"] == "rejected"
    assert "capture age" in rejected["reason"]
    assert bridge.leases.list() == []


def test_control_websocket_stops_the_lease_when_a_manual_ack_is_missing():
    bridge = Bridge(feature_gates={"manual_control_v1": True})
    transport = FakeTransport(
        capabilities={"manual_control_v1", "status", "motion", "safety"},
        auto_ack=False,
    )
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        with client.websocket_connect("/api/v1/control") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            lease = websocket.receive_json()["lease"]
            websocket.send_json(
                {
                    "type": "input",
                    "lease_id": lease["lease_id"],
                    "input_seq": 1,
                    "action": "input",
                    "direction": {"yaw": 0, "pitch": 0},
                }
            )
            timed_out = websocket.receive_json()

    assert timed_out["command"]["state"] == "timeout"
    assert timed_out["command"]["error"] == {"code": "timeout", "reason": "manual_ack_timeout"}
    assert bridge.get_control_lease(lease["lease_id"]).state is LeaseState.RELEASED
    assert bridge.get_control_lease(lease["lease_id"]).reason == "device_ack_timeout"


def test_camera_preview_cannot_start_while_a_manual_lease_is_active():
    bridge = Bridge(feature_gates={"manual_control_v1": True, "media": True})
    transport = FakeTransport(capabilities={"manual_control_v1", "status", "motion", "safety", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    bridge.acquire_control_lease(device.device_id, "test-connection")

    with TestClient(create_app(bridge)) as client:
        response = client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "release manual control before starting camera preview"
    assert not any(frame.get("type") == "command.camera_preview" for frame in transport.sent_frames)


def test_control_websocket_refuses_manual_lease_when_camera_stop_is_rejected():
    bridge = Bridge(feature_gates={"manual_control_v1": True, "media": True})
    transport = FakeTransport(capabilities={"manual_control_v1", "status", "motion", "safety", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        started = client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        )
        assert started.status_code == 202
        transport.reject_commands["command.camera_preview"] = "camera_stop_rejected"
        with client.websocket_connect("/api/v1/control") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            rejected = websocket.receive_json()

    assert rejected == {
        "type": "control.error",
        "code": "rejected",
        "reason": "camera preview did not stop before manual control",
    }
    assert bridge._active_camera_previews


def test_control_websocket_reports_feature_gate_without_sending_a_wire_command():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    with TestClient(create_app(bridge)) as client:
        with client.websocket_connect("/api/v1/control") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            error = websocket.receive_json()
    assert error["code"] == "capability_unavailable"
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)


def test_control_websocket_close_releases_active_lease():
    bridge = Bridge(feature_gates={"manual_control_v1": True})
    transport = FakeTransport(capabilities={"manual_control_v1"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        with client.websocket_connect("/api/v1/control") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "lease.acquire", "device_id": device.device_id})
            lease = websocket.receive_json()["lease"]

    released = bridge.get_control_lease(lease["lease_id"])
    assert released.state is LeaseState.RELEASED
    assert released.reason == "websocket_closed"


def test_semantic_behavior_and_speech_commands_are_gated_at_api_boundary():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        behavior = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "behavior.play", "params": {"name": "greet"}},
        )
        speech = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "speech.play", "params": {"text": "hello", "voice": "calm"}},
        )
        raw = client.post(
            f"/api/v1/devices/{device.device_id}/commands",
            json={"type": "behavior.play", "params": {"name": "greet", "yaw_deg": 10}},
        )

    assert behavior.status_code == 409
    assert behavior.json()["error"]["required_capability"] == "behavior"
    assert speech.status_code == 409
    assert speech.json()["error"]["required_capability"] == "speech"
    assert raw.status_code == 422
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)


def test_batch_api_keeps_per_device_outcomes_and_does_not_rollback_success():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        created = client.post(
            "/api/v1/batch-tasks",
            json={
                "device_ids": [device.device_id, "offline-device"],
                "command_type": "control.status",
            },
        )
        task = created.json()
        fetched = client.get(f"/api/v1/batch-tasks/{task['task_id']}")
        cancelled = client.post(f"/api/v1/batch-tasks/{task['task_id']}/cancel")

    assert created.status_code == 202
    assert task["aggregate_state"] == "partial"
    assert [target["state"] for target in task["targets"]] == ["completed", "offline"]
    assert fetched.status_code == 200
    assert fetched.json()["task_id"] == task["task_id"]
    assert cancelled.status_code == 200
    assert cancelled.json()["aggregate_state"] == "partial"
