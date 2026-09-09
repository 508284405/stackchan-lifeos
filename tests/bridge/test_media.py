"""Camera preview frame assembly and browser media boundary tests."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from fastapi.testclient import TestClient

from bridge import Bridge, FakeTransport
from bridge.api import create_app
from bridge.domain import CommandState
from bridge.media import CameraFrameStore


def setup_media_bridge():
    bridge = Bridge(feature_gates={"media": True})
    transport = FakeTransport(capabilities={"status", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    return bridge, transport, device


def test_bridge_uses_an_explicitly_configured_camera_preview_rate():
    bridge = Bridge(feature_gates={"media": True}, camera_preview_fps=2)
    mapped = bridge.map_web_command(
        "camera.preview.start",
        {},
        issued_at_ms=1,
        expires_at_ms=10_001,
        camera_preview_fps=bridge.camera_preview_fps,
    )
    assert mapped.payload == {"action": "start", "fps": 2, "duration_ms": 0}


def test_camera_frame_wait_wakes_on_publish_without_polling_delay():
    async def scenario():
        store = CameraFrameStore()
        jpeg = b"\xff\xd8\xff\xd9"
        store.begin(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-1",
            width=320,
            height=240,
            size=len(jpeg),
            chunk_count=1,
            timestamp_ms=1,
        )
        store.add_chunk(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-1",
            index=0,
            chunk_count=1,
            data=base64.b64encode(jpeg).decode("ascii"),
        )
        waiter = asyncio.create_task(
            store.wait_for("device-1", session_id="session-1", timeout_s=1.0)
        )
        await asyncio.sleep(0)
        with patch("bridge.media.asyncio.sleep", side_effect=AssertionError("polling")):
            frame = await store.finish(
                device_id="device-1",
                session_id="session-1",
                frame_id="frame-1",
                size=len(jpeg),
            )
            waited = await waiter
        return frame, waited

    frame, waited = asyncio.run(scenario())
    assert frame.frame_id == "frame-1"
    assert waited is not None
    assert waited.frame_id == "frame-1"


def test_camera_new_begin_supersedes_an_incomplete_frame():
    async def scenario():
        store = CameraFrameStore()
        jpeg = b"\xff\xd8\xff\xd9"
        store.begin(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-1",
            width=320,
            height=240,
            size=len(jpeg),
            chunk_count=2,
            timestamp_ms=1,
        )
        store.begin(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-2",
            width=320,
            height=240,
            size=len(jpeg),
            chunk_count=1,
            timestamp_ms=2,
        )
        store.add_chunk(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-2",
            index=0,
            chunk_count=1,
            data=base64.b64encode(jpeg).decode("ascii"),
        )
        return await store.finish(
            device_id="device-1",
            session_id="session-1",
            frame_id="frame-2",
            size=len(jpeg),
        )

    frame = asyncio.run(scenario())
    assert frame.frame_id == "frame-2"
    assert frame.data == b"\xff\xd8\xff\xd9"


def test_camera_sequence_gap_resyncs_without_offlining_the_device():
    async def scenario():
        bridge, transport, device = setup_media_bridge()
        transport.auto_ack = False
        session = await bridge.connect(device.device_id, transport)
        await bridge.submit_camera_preview(device.device_id, "start")
        jpeg = b"\xff\xd8\xff\xd9"
        first_seq = bridge.get_session(session.session_id).rx_seq + 2
        assert await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "camera.frame.begin",
                "event_id": "gap-begin",
                "device_id": device.device_id,
                "seq": first_seq,
                "ts_ms": 1,
                "payload": {
                    "frame_id": "gap-frame",
                    "format": "jpeg",
                    "width": 320,
                    "height": 240,
                    "size": len(jpeg),
                    "chunk_count": 1,
                },
            },
        )
        assert await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "camera.frame.chunk",
                "event_id": "gap-chunk",
                "device_id": device.device_id,
                "seq": first_seq + 1,
                "ts_ms": 2,
                "payload": {
                    "frame_id": "gap-frame",
                    "index": 0,
                    "chunk_count": 1,
                    "data": base64.b64encode(jpeg).decode("ascii"),
                },
            },
        )
        assert await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "camera.frame.end",
                "event_id": "gap-end",
                "device_id": device.device_id,
                "seq": first_seq + 2,
                "ts_ms": 3,
                "payload": {"frame_id": "gap-frame", "size": len(jpeg)},
            },
        )
        return bridge, session, device, first_seq

    bridge, session, device, first_seq = asyncio.run(scenario())
    assert bridge.get_session(session.session_id).state.value == "online"
    assert bridge.get_session(session.session_id).rx_seq == first_seq + 2
    assert any(
        item.kind.value == "media.sequence.resynced"
        for item in bridge.store.list_audits(device_id=device.device_id)
    )


def test_firmware_preview_uses_native_color_latest_double_buffer():
    source = Path("firmware/src/hal/stackchan/stackchan.cpp").read_text(encoding="utf-8")
    app_main = Path("firmware/idf/main/app_main.cpp").read_text(encoding="utf-8")
    assert "config.pixel_format = PIXFORMAT_RGB565;" in source
    assert "kCameraColorMinimumIntervalMs = 150" in app_main
    assert "config.fb_count = 2;" in source
    assert "config.grab_mode = CAMERA_GRAB_LATEST;" in source
    assert "config.pixel_format = PIXFORMAT_YUV422;" not in source
    assert "config.pixel_format = PIXFORMAT_GRAYSCALE;" not in source
    assert "sensor->set_exposure_ctrl(sensor, 1)" in source
    assert "sensor->set_gain_ctrl(sensor, 1)" in source
    assert "sensor->set_ae_level(sensor, 2)" in source
    assert "usb_serial_jtag_write_bytes(output_line, written" in app_main
    assert "usb_serial_jtag_write_bytes(line, length" in app_main
    assert "std::fwrite(output_line" not in app_main
    assert "bool bounded = true" in app_main
    assert "kDiagnosticWriteWaitTicks = 0" in app_main
    assert "xSemaphoreTake(output_mutex, kDiagnosticWriteWaitTicks)" in app_main
    assert "usb_serial_jtag_write_bytes(line, length, kDiagnosticWriteWaitTicks)" in app_main
    assert "kCameraWriteTimeoutMs = 20" in app_main
    assert ".tx_buffer_size = kUsbTxBufferBytes" in app_main
    assert "if (sent) output_sequence = next_sequence;" in app_main


def test_fake_camera_preview_streams_complete_frames_without_persistence():
    async def scenario():
        bridge, transport, device = setup_media_bridge()
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_camera_preview(device.device_id, "start")
        for _ in range(20):
            frame = bridge.latest_camera_frame(device.device_id, session_id=session.session_id)
            if frame is not None:
                break
            await asyncio.sleep(0.05)
        stop = await bridge.submit_camera_preview(device.device_id, "stop")
        await bridge.disconnect(session.session_id)
        return bridge, transport, command, stop, frame

    bridge, transport, command, stop, frame = asyncio.run(scenario())
    assert command.state is CommandState.COMPLETED
    assert stop.state is CommandState.COMPLETED
    assert any(
        item.get("kind") == "event" and item.get("type") == "host.heartbeat"
        for item in transport.sent_frames
    )
    start_frames = [
        item for item in transport.sent_frames
        if item.get("type") == "command.camera_preview"
        and item.get("payload", {}).get("action") == "start"
    ]
    assert start_frames
    assert start_frames[-1]["payload"] == {"action": "start", "fps": 10, "duration_ms": 0}
    assert frame is not None
    assert frame.data.startswith(b"\xff\xd8")
    assert frame.data.endswith(b"\xff\xd9")
    assert frame.width == 320
    assert frame.height == 240
    assert not bridge.camera_frames.latest("stackchan-fake-01")
    assert all("data" not in event.payload for event in bridge.events.since(0))
    assert not any(event.type.startswith("camera.frame") for event in bridge.events.since(0))
    assert not any("camera.frame" in str(item.payload) for item in bridge.store.list_audits())


def test_camera_start_acceptance_uses_preview_lifetime_not_command_ttl():
    async def scenario():
        bridge = Bridge(feature_gates={"media": True})
        transport = FakeTransport(capabilities={"status", "camera"}, auto_ack=False)
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        session = await bridge.connect(device.device_id, transport)
        start = await bridge.submit_camera_preview(device.device_id, "start")
        await transport.acknowledge_pending(start.command_id, status="accepted")
        jpeg = b"\xff\xd8\xff\xd9"
        await transport.emit(
            kind="event",
            type="camera.frame.begin",
            payload={
                "frame_id": "frame-1",
                "format": "jpeg",
                "width": 320,
                "height": 240,
                "size": len(jpeg),
                "chunk_count": 1,
            },
        )
        await transport.emit(
            kind="event",
            type="camera.frame.chunk",
            payload={
                "frame_id": "frame-1",
                "index": 0,
                "chunk_count": 1,
                "data": base64.b64encode(jpeg).decode("ascii"),
            },
        )
        await transport.emit(
            kind="event",
            type="camera.frame.end",
            payload={"frame_id": "frame-1", "size": len(jpeg)},
        )
        current = bridge.get_command(start.command_id)
        bridge.expire_due(now_ms=current.expires_at_ms + 31_000)
        stable = bridge.get_command(start.command_id)
        assert bridge._active_camera_previews.get(device.device_id) == session.session_id
        stop = await bridge.submit_camera_preview(device.device_id, "stop")
        # A sent stop is not enough to hand the USB link to another mode. The
        # preview remains active until the device acknowledges that bounded
        # transition.
        assert bridge._active_camera_previews.get(device.device_id) == session.session_id
        await transport.acknowledge_pending(stop.command_id, status="accepted")
        stopped = bridge.get_command(stop.command_id)
        await bridge.stop_supervisor()
        return bridge, stable, stopped

    bridge, start, stop = asyncio.run(scenario())
    assert start.state is CommandState.COMPLETED
    assert start.result == {"status": "completed", "frame_id": "frame-1"}
    assert stop.state is CommandState.COMPLETED
    assert not bridge._active_camera_previews


def test_restarting_camera_preview_does_not_deactivate_replacement_heartbeat():
    async def scenario():
        bridge, transport, device = setup_media_bridge()
        session = await bridge.connect(device.device_id, transport)
        first = await bridge.submit_camera_preview(device.device_id, "start")
        first_heartbeat = bridge._camera_heartbeat_tasks[device.device_id]

        second = await bridge.submit_camera_preview(device.device_id, "start")
        await asyncio.sleep(0)
        replacement_heartbeat = bridge._camera_heartbeat_tasks.get(device.device_id)
        active_session_id = bridge._active_camera_previews.get(device.device_id)
        stop = await bridge.submit_camera_preview(device.device_id, "stop")
        await bridge.stop_supervisor()
        return (
            first,
            second,
            stop,
            session,
            first_heartbeat,
            replacement_heartbeat,
            active_session_id,
        )

    (
        first,
        second,
        stop,
        session,
        first_heartbeat,
        replacement_heartbeat,
        active_session_id,
    ) = asyncio.run(scenario())
    assert first.state is CommandState.COMPLETED
    assert second.state is CommandState.COMPLETED
    assert stop.state is CommandState.COMPLETED
    assert first_heartbeat is not replacement_heartbeat
    assert replacement_heartbeat is not None
    assert active_session_id == session.session_id


def test_camera_frame_with_missing_chunk_is_rejected_and_not_published():
    async def scenario():
        bridge, transport, device = setup_media_bridge()
        session = await bridge.connect(device.device_id, transport)
        await bridge.submit_camera_preview(device.device_id, "start")
        assert await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "camera.frame.begin",
                "event_id": "frame-begin-1",
                "device_id": device.device_id,
                "seq": bridge.get_session(session.session_id).rx_seq + 1,
                "ts_ms": 1,
                "payload": {
                    "frame_id": "frame-1",
                    "format": "jpeg",
                    "width": 320,
                    "height": 240,
                    "size": 4,
                    "chunk_count": 2,
                },
            }
        )
        return bridge, session, device

    bridge, session, device = asyncio.run(scenario())
    result = asyncio.run(
        bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "camera.frame.end",
                "event_id": "frame-end-1",
                "device_id": device.device_id,
                "seq": bridge.get_session(session.session_id).rx_seq + 1,
                "ts_ms": 2,
                "payload": {"frame_id": "frame-1", "size": 4},
            },
        )
    )
    assert result is False
    assert bridge.latest_camera_frame(device.device_id) is None
    assert any(
        item.kind.value == "protocol.rejected" and item.payload.get("media") == "camera_frame"
        for item in bridge.store.list_audits(device_id=device.device_id)
    )


def test_camera_preview_api_returns_mjpeg_parts_and_keeps_origin_boundary():
    async def scenario():
        bridge, transport, device = setup_media_bridge()
        await bridge.connect(device.device_id, transport)
        started = await bridge.submit_camera_preview(device.device_id, "start")
        for _ in range(20):
            if bridge.latest_camera_frame(device.device_id) is not None:
                break
            await asyncio.sleep(0.05)

        app = create_app(bridge)
        route = next(item for item in app.routes if getattr(item, "path", "") == "/api/v1/devices/{device_id}/camera/stream")
        request = Request({
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/devices/{device.device_id}/camera/stream",
            "headers": [],
            "query_string": b"",
        })
        response = await route.endpoint(device.device_id, request)
        first = await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        stopped = await bridge.submit_camera_preview(device.device_id, "stop")
        return started, stopped, response, first

    started, stopped, response, first = asyncio.run(scenario())
    assert started.state is CommandState.COMPLETED
    assert stopped.state is CommandState.COMPLETED
    assert response.media_type.startswith("multipart/x-mixed-replace")
    assert response.headers["content-type"].startswith("multipart/x-mixed-replace; boundary=lifeos-frame")
    assert b"Content-Type: image/jpeg" in first
    assert b"\xff\xd8" in first


def test_camera_preview_is_feature_gated_before_device_wire_dispatch():
    bridge = Bridge(feature_gates={"media": False})
    transport = FakeTransport(capabilities={"status", "camera"})
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    with TestClient(create_app(bridge)) as client:
        response = client.post(
            f"/api/v1/devices/{device.device_id}/camera-preview",
            json={"action": "start"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "capability_unavailable"
    assert not any(frame.get("type") == "command.camera_preview" for frame in transport.sent_frames)


def test_device_error_detail_is_retained_for_camera_diagnostics():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport(auto_ack=False)
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        session = await bridge.connect(device.device_id, transport)
        command = await bridge.submit_command(device.device_id, "control.status")
        await bridge.receive(
            session.session_id,
            {
                "schema": "lifeos.v1",
                "kind": "error",
                "type": "error.protocol",
                "event_id": "device-error-1",
                "correlation_id": command.command_id,
                "device_id": device.device_id,
                "seq": session.rx_seq + 1,
                "ts_ms": 1,
                "payload": {"code": "unsupported", "detail": "camera_unavailable"},
            },
        )
        return bridge.get_command(command.command_id)

    command = asyncio.run(scenario())
    assert command.state is CommandState.REJECTED
    assert command.error == {"code": "unsupported", "detail": "camera_unavailable"}
