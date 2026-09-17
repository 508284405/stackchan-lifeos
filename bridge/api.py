"""Browser REST/media boundary; browser DTOs never become raw wire frames."""

from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .domain import CommandState
from .errors import (
    CapabilityUnavailable,
    ConflictError,
    NotFoundError,
    TransportError,
    ValidationError,
)
from .events import CursorExpired
from .scan import add_usb_device, scan_usb_ports
from .service import Bridge


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = Field(default=1_500, ge=1, le=1_500)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=96)
    correlation_id: Optional[str] = Field(default=None, min_length=1, max_length=96)


class EmergencyStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=128)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=96)
    correlation_id: Optional[str] = Field(default=None, min_length=1, max_length=96)


class CameraPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "stop"]


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[str] = Field(min_length=1, max_length=200)
    command_type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = Field(default=1_500, ge=1, le=1_500)
    deadline_ms: int = Field(default=5_000, ge=1, le=60_000)


class MaintenancePrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=64)
    ttl_ms: int = Field(default=60_000, ge=1, le=300_000)


class RolloutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_ref: str = Field(min_length=1, max_length=256)


class RolloutPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RolloutBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_refs: dict[str, str] = Field(min_length=1, max_length=200)


class UsbAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=96)
    hardware_id: str = Field(min_length=1, max_length=96)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=96)


def _device_payload(bridge: Bridge, device) -> dict[str, Any]:
    session = bridge.latest_session_for_device(device.device_id)
    data = device.to_dict()
    data["session"] = session.to_dict() if session else None
    data["health"] = bridge.latest_health_for_device(device.device_id)
    return data


def _command_payload(command) -> dict[str, Any]:
    return command.to_dict()


def _lease_payload(lease) -> dict[str, Any]:
    return lease.to_dict()


def create_app(
    bridge: Bridge | None = None,
    *,
    bind_host: str = "127.0.0.1",
    web_no_auth_trusted_lan: bool = False,
    origin_allowlist: Optional[set[str]] = None,
) -> FastAPI:
    """Create the W1 API with an explicit safe default binding policy."""

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if bind_host in {"0.0.0.0", "::"}:
        raise ValueError("wildcard binding is not allowed for the unauthenticated Web Bridge")
    if bind_host not in loopback_hosts:
        if not web_no_auth_trusted_lan:
            raise ValueError("non-loopback binding requires web_no_auth_trusted_lan=true")
        try:
            address = ipaddress.ip_address(bind_host)
        except ValueError as exc:
            raise ValueError("trusted-LAN binding must use a concrete private IP") from exc
        if not address.is_private:
            raise ValueError("trusted-LAN binding must use a private IP")
    bridge = bridge or Bridge()
    configured_origins = None if origin_allowlist is None else set(origin_allowlist)
    high_impact_gates = {
        "manual_control_v1",
        "maintenance",
        "firmware_rollout",
        "factory_reset",
        "media",
        "usb_add",
    }
    if configured_origins is not None:
        if any(not isinstance(origin, str) or not origin or origin == "*" for origin in configured_origins):
            raise ValueError("origin_allowlist must contain explicit non-wildcard origins")
    if bind_host not in loopback_hosts and not configured_origins and any(
        bridge.feature_gates.get(gate, False) for gate in high_impact_gates
    ):
        raise ValueError("trusted-LAN high-impact features require a non-empty origin_allowlist")
    app = FastAPI(title="StackChan LifeOS Web Bridge", version="0.1.0")
    app.state.bridge = bridge

    @app.on_event("startup")
    async def start_bridge_supervisor() -> None:
        await bridge.start_supervisor()

    @app.on_event("shutdown")
    async def stop_bridge_supervisor() -> None:
        await bridge.stop_supervisor()

    def _require_feature(gate: str) -> None:
        if not bridge.feature_gates.get(gate, False):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "capability_unavailable",
                        "reason": "feature_gate_disabled",
                        "required_capability": gate,
                    }
                },
            )

    app.state.bind_host = bind_host
    app.state.web_no_auth_trusted_lan = web_no_auth_trusted_lan
    app.state.origin_allowlist = configured_origins
    if configured_origins is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(configured_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        devices = bridge.registry.list()
        online = sum(1 for device in devices if bridge.active_session_for_device(device.device_id))
        return {
            "ok": True,
            "service": "web_bridge",
            "bind_host": bind_host,
            "web_no_auth_trusted_lan": web_no_auth_trusted_lan,
            "registered_devices": len(devices),
            "online_devices": online,
            "origin_allowlist_configured": configured_origins is not None and bool(configured_origins),
            "feature_gates": {
                "control": bool(bridge.feature_gates.get("control", False)),
                "status": bool(bridge.feature_gates.get("status", False)),
                "motion": bool(bridge.feature_gates.get("motion", False)),
                "safety": bool(bridge.feature_gates.get("safety", False)),
                "emergency_stop": bool(bridge.feature_gates.get("emergency_stop", False)),
                "manual_control_v1": bool(bridge.feature_gates.get("manual_control_v1", False)),
                "manual_camera_preview": bool(bridge.feature_gates.get("manual_camera_preview", False)),
                "media": bool(bridge.feature_gates.get("media", False)),
                "behavior": bool(bridge.feature_gates.get("behavior", False)),
                "speech": bool(bridge.feature_gates.get("speech", False)),
                "maintenance": bool(bridge.feature_gates.get("maintenance", False)),
                "firmware_rollout": bool(bridge.feature_gates.get("firmware_rollout", False)),
                "usb_add": bool(bridge.feature_gates.get("usb_add", False)),
                "diagnostics": True,
            },
        }

    @app.get("/api/v1/devices")
    async def devices() -> dict[str, Any]:
        return {"items": [_device_payload(bridge, device) for device in bridge.registry.list()]}

    @app.get("/api/v1/devices/{device_id}")
    async def device_detail(device_id: str) -> dict[str, Any]:
        try:
            return _device_payload(bridge, bridge.registry.get(device_id))
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/devices/{device_id}/claim")
    async def claim(device_id: str, request: ClaimRequest) -> dict[str, Any]:
        try:
            candidate = bridge.registry.get_candidate(request.candidate_id)
            if candidate.device_id != device_id:
                raise HTTPException(status_code=409, detail="candidate identity does not match device path")
            record = bridge.claim(request.candidate_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _device_payload(bridge, record)

    @app.post("/api/v1/usb-scan")
    async def usb_scan() -> dict[str, Any]:
        """Enumerate local USB serial ports; probes only unclaimed ports."""

        _require_feature("usb_add")
        return {"items": await scan_usb_ports(bridge)}

    @app.post("/api/v1/usb-devices")
    async def usb_add_device(request: UsbAddRequest):
        """Register and connect one scanned device; identity comes from a scan."""

        _require_feature("usb_add")
        try:
            record = await add_usb_device(
                bridge,
                path=request.path,
                device_id=request.device_id,
                hardware_id=request.hardware_id,
                display_name=request.display_name,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TransportError as exc:
            raise HTTPException(status_code=502, detail=f"usb connect failed: {exc}") from exc
        return _device_payload(bridge, record)

    @app.post("/api/v1/devices/{device_id}/commands", status_code=202)
    async def commands(device_id: str, request: CommandRequest):
        try:
            if request.type == "behavior.play":
                if set(request.params) - {"name", "intensity", "duration_ms"}:
                    raise ValidationError("behavior.play accepts only name, intensity, and duration_ms")
                command = await bridge.submit_behavior(
                    device_id,
                    name=request.params.get("name"),
                    intensity=request.params.get("intensity", 0.5),
                    duration_ms=request.params.get("duration_ms", 1_000),
                    ttl_ms=request.ttl_ms,
                    idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                )
            elif request.type == "speech.play":
                if set(request.params) - {"text", "voice"}:
                    raise ValidationError("speech.play accepts only text and voice")
                command = await bridge.submit_speech(
                    device_id,
                    text=request.params.get("text"),
                    voice=request.params.get("voice", "default"),
                    ttl_ms=request.ttl_ms,
                    idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                )
            else:
                command = await bridge.submit_command(
                    device_id,
                    request.type,
                    params=request.params,
                    ttl_ms=request.ttl_ms,
                    idempotency_key=request.idempotency_key,
                    correlation_id=request.correlation_id,
                )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConflictError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if command.state is CommandState.REJECTED and command.error:
            if command.error.get("code") == "capability_unavailable":
                return JSONResponse(status_code=409, content=_command_payload(command))
            if command.error.get("code") == "validation_error":
                return JSONResponse(status_code=422, content=_command_payload(command))
        return _command_payload(command)

    @app.post("/api/v1/devices/{device_id}/maintenance/prepare", status_code=202)
    async def maintenance_prepare(device_id: str, request: MaintenancePrepareRequest):
        try:
            return (await bridge.prepare_maintenance(device_id, request.operation, ttl_ms=request.ttl_ms)).to_dict()
        except NotFoundError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityUnavailable as exc: raise HTTPException(status_code=409, detail={"error": {"code": exc.code, "reason": exc.reason, "required_capability": exc.required_capability}}) from exc
        except (ConflictError, ValidationError) as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/maintenance-tasks/{task_id}/execute", status_code=202)
    async def maintenance_execute(task_id: str):
        try: return (await bridge.execute_maintenance(task_id)).to_dict()
        except CapabilityUnavailable as exc: raise HTTPException(status_code=409, detail={"error": {"code": exc.code, "reason": exc.reason, "required_capability": exc.required_capability}}) from exc
        except ConflictError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/maintenance-tasks/{task_id}")
    async def maintenance_task(task_id: str):
        try: return bridge.get_maintenance_task(task_id).to_dict()
        except ConflictError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/devices/{device_id}/diagnostics")
    async def diagnostics(device_id: str):
        try: return bridge.export_diagnostics(device_id)
        except NotFoundError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/devices/{device_id}/rollout-tasks", status_code=202)
    async def rollout_create(device_id: str, request: RolloutRequest):
        try: return bridge.create_rollout(device_id, request.image_ref).to_dict()
        except NotFoundError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/rollout-tasks/{task_id}/preflight", status_code=202)
    async def rollout_preflight(task_id: str, request: RolloutPreflightRequest):
        try:
            return (await bridge.preflight_rollout(task_id)).to_dict()
        except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityUnavailable as exc: raise HTTPException(status_code=409, detail=exc.reason) from exc
        except (ConflictError, ValidationError) as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/rollout-tasks/{task_id}/execute", status_code=202)
    async def rollout_execute(task_id: str):
        try: return (await bridge.execute_rollout(task_id)).to_dict()
        except CapabilityUnavailable as exc: raise HTTPException(status_code=409, detail={"error": {"code": exc.code, "reason": exc.reason, "required_capability": exc.required_capability}}) from exc
        except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/rollout-tasks/{task_id}")
    async def rollout_task(task_id: str):
        try: return bridge.get_rollout(task_id).to_dict()
        except KeyError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/rollout-batches", status_code=202)
    async def rollout_batch_create(request: RolloutBatchRequest):
        try:
            return bridge.create_rollout_batch(request.artifact_refs).to_dict()
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/rollout-batches/{task_id}/resume", status_code=202)
    async def rollout_batch_resume(task_id: str):
        try:
            return (await bridge.resume_rollout_batch(task_id)).to_dict()
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/rollout-batches/{task_id}")
    async def rollout_batch_get(task_id: str):
        try:
            return bridge.get_rollout_batch(task_id).to_dict()
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    @app.post("/api/v1/devices/{device_id}/emergency-stop", status_code=202)
    async def emergency_stop(device_id: str, request: EmergencyStopRequest):
        try:
            command = await bridge.submit_emergency_stop(
                device_id,
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if command.state is CommandState.REJECTED and command.error:
            if command.error.get("code") == "capability_unavailable":
                return JSONResponse(status_code=409, content=_command_payload(command))
        return _command_payload(command)

    @app.post("/api/v1/devices/{device_id}/camera-preview", status_code=202)
    async def camera_preview(device_id: str, request: CameraPreviewRequest):
        """Explicitly start/stop the bounded, non-recording camera preview."""

        _require_feature("media")
        try:
            command = await bridge.submit_camera_preview(device_id, request.action)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityUnavailable as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": exc.code,
                        "reason": exc.reason,
                        "required_capability": exc.required_capability,
                    }
                },
            ) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _command_payload(command)

    @app.get("/api/v1/devices/{device_id}/camera/stream")
    async def camera_stream(device_id: str, request: Request, viewer_id: str):
        """Stream the latest complete JPEG frames as a bounded MJPEG response."""

        _require_feature("media")
        origin = request.headers.get("origin")
        if configured_origins is not None and origin is not None and origin not in configured_origins:
            raise HTTPException(status_code=403, detail="request origin is not allowlisted")
        try:
            session = bridge.camera_preview_session(device_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityUnavailable as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": exc.code,
                        "reason": exc.reason,
                        "required_capability": exc.required_capability,
                    }
                },
            ) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def body():
            frame_id: str | None = None
            empty_polls = 0
            while True:
                current = bridge.active_session_for_device(device_id)
                if current is None or current.session_id != session.session_id:
                    return
                frame = await bridge.wait_for_camera_frame(
                    device_id,
                    session_id=session.session_id,
                    after_frame_id=frame_id,
                    timeout_s=3.0,
                )
                if frame is None:
                    empty_polls += 1
                    if empty_polls >= 3:
                        return
                    continue
                empty_polls = 0
                frame_id = frame.frame_id
                try:
                    bridge.camera_viewers.deliver(viewer_id, frame, now_ms=bridge._clock_ms())
                except Exception:
                    return
                yield (
                    b"--lifeos-frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {frame.size}\r\n".encode("ascii")
                    + f"X-LifeOS-Frame-Id: {frame.frame_id}\r\n".encode("ascii")
                    + f"X-LifeOS-Frame-Token: {frame.token}\r\n\r\n".encode("ascii")
                    + frame.data
                    + b"\r\n"
                )

        return StreamingResponse(
            body(),
            media_type="multipart/x-mixed-replace; boundary=lifeos-frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/devices/{device_id}/camera/frame")
    async def camera_frame(device_id: str, request: Request, viewer_id: str):
        """Return one latest frame so the UI can acknowledge actual display."""

        _require_feature("media")
        origin = request.headers.get("origin")
        if configured_origins is not None and origin is not None and origin not in configured_origins:
            raise HTTPException(status_code=403, detail="request origin is not allowlisted")
        try:
            frame = bridge.camera_frame_for_viewer(device_id, viewer_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilityUnavailable as exc:
            raise HTTPException(status_code=409, detail=exc.reason) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            content=frame.data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "X-LifeOS-Frame-Id": frame.frame_id,
                "X-LifeOS-Frame-Token": frame.token,
                "X-LifeOS-Capture-Ts-Ms": str(frame.capture_timestamp_ms),
            },
        )

    @app.get("/api/v1/commands/{command_id}")
    async def get_command(command_id: str) -> dict[str, Any]:
        try:
            return _command_payload(bridge.get_command(command_id))
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/batch-tasks", status_code=202)
    async def create_batch(request: BatchRequest):
        try:
            task = await bridge.submit_batch(
                request.device_ids,
                request.command_type,
                params=request.params,
                ttl_ms=request.ttl_ms,
                deadline_ms=request.deadline_ms,
            )
        except (ValidationError, ConflictError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return task.to_dict()

    @app.get("/api/v1/batch-tasks/{task_id}")
    async def get_batch(task_id: str):
        try:
            return bridge.get_batch(task_id).to_dict()
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/batch-tasks/{task_id}/cancel")
    async def cancel_batch(task_id: str):
        try:
            return bridge.cancel_batch(task_id).to_dict()
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket):
        """Push filtered domain events; clients resync through REST on cursor expiry."""

        if configured_origins is not None and websocket.headers.get("origin") not in configured_origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            subscription = await websocket.receive_json()
            if not isinstance(subscription, dict):
                await websocket.send_json({"type": "subscription.error", "reason": "object_required"})
                await websocket.close(code=1003)
                return
            device_ids = subscription.get("device_ids")
            visible_devices = subscription.get("visible_devices") is True
            if device_ids is not None and (
                not isinstance(device_ids, list)
                or not device_ids
                or not all(isinstance(device_id, str) and device_id for device_id in device_ids)
            ):
                await websocket.send_json({"type": "subscription.error", "reason": "invalid_device_ids"})
                await websocket.close(code=1003)
                return
            if device_ids is None and not visible_devices:
                await websocket.send_json(
                    {"type": "subscription.error", "reason": "device_filter_required"}
                )
                await websocket.close(code=1003)
                return
            cursor: str | int | None = subscription.get("cursor", 0)
            include_telemetry = subscription.get("include_telemetry", True) is True
            while True:
                try:
                    pending = bridge.events.since(
                        cursor,
                        device_ids=device_ids,
                        include_telemetry=include_telemetry,
                    )
                except CursorExpired:
                    await websocket.send_json({"type": "resync_required", "reason": "cursor_expired"})
                    cursor = bridge.events.latest_cursor
                    continue
                for event in pending:
                    await websocket.send_json(event.to_dict())
                    cursor = event.cursor
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if not isinstance(message, dict):
                    await websocket.send_json({"type": "subscription.error", "reason": "object_required"})
                    continue
                if "cursor" in message:
                    cursor = message["cursor"]
                if "device_ids" in message:
                    next_devices = message["device_ids"]
                    if isinstance(next_devices, list) and next_devices and all(
                        isinstance(device_id, str) and device_id for device_id in next_devices
                    ):
                        device_ids = next_devices
                if "include_telemetry" in message:
                    include_telemetry = message["include_telemetry"] is True
        except WebSocketDisconnect:
            return
        except (ValueError, TypeError):
            await websocket.send_json({"type": "subscription.error", "reason": "invalid_subscription"})
            await websocket.close(code=1003)

    @app.websocket("/api/v1/control")
    async def control(websocket: WebSocket):
        """W3 control socket; it exposes only lease/input DTOs, never raw wire data."""

        if configured_origins is not None and websocket.headers.get("origin") not in configured_origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection_id = f"control-{uuid4()}"
        bridge.open_control_connection(connection_id)
        await websocket.send_json({"type": "control.connected", "connection_id": connection_id})
        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                except asyncio.TimeoutError:
                    bridge.reap_control_leases()
                    await bridge.enforce_manual_video_freshness()
                    continue
                if not isinstance(message, dict):
                    await websocket.send_json({"type": "control.error", "code": "invalid_request"})
                    continue
                operation = message.get("type")
                try:
                    if operation == "lease.acquire":
                        lease, camera_preview_stopped = await bridge.acquire_control_lease_with_camera_interlock(
                            message["device_id"],
                            connection_id,
                            ttl_ms=message.get("ttl_ms", 400),
                            max_duration_ms=message.get("max_duration_ms", 30_000),
                        )
                        await websocket.send_json(
                            {
                                "type": "lease.acquired",
                                "lease": _lease_payload(lease),
                                "camera_preview_stopped": camera_preview_stopped,
                            }
                        )
                    elif operation == "viewer.open":
                        viewer = await bridge.open_camera_viewer(
                            message["device_id"],
                            connection_id,
                        )
                        await websocket.send_json(
                            {
                                "type": "viewer.opened",
                                "viewer_id": viewer.viewer_id,
                                "device_id": viewer.device_id,
                                "session_id": viewer.session_id,
                            }
                        )
                    elif operation == "viewer.close":
                        await bridge.close_camera_viewer(connection_id)
                        await websocket.send_json({"type": "viewer.closed", "viewer_id": connection_id})
                    elif operation == "video.displayed":
                        viewer = bridge.acknowledge_camera_display(
                            device_id=message["device_id"],
                            viewer_id=connection_id,
                            frame_id=message["frame_id"],
                            token=message["token"],
                            visible=message.get("visible") is True,
                        )
                        await websocket.send_json(
                            {
                                "type": "video.display.acknowledged",
                                "viewer_id": viewer.viewer_id,
                                "frame_id": viewer.displayed_frame_id,
                            }
                        )
                    elif operation == "lease.renew":
                        lease = bridge.renew_control_lease(
                            message["lease_id"],
                            connection_id,
                            ttl_ms=message.get("ttl_ms", 400),
                        )
                        await websocket.send_json({"type": "lease.renewed", "lease": _lease_payload(lease)})
                    elif operation == "lease.release":
                        lease = bridge.release_control_lease(message["lease_id"], connection_id)
                        await websocket.send_json({"type": "lease.released", "lease": _lease_payload(lease)})
                    elif operation == "input":
                        command = await bridge.submit_control_input(
                            message["lease_id"],
                            connection_id,
                            input_seq=message["input_seq"],
                            action=message["action"],
                            direction=message.get("direction"),
                            ttl_ms=message.get("ttl_ms", 400),
                        )
                        command = await bridge.await_control_input_ack(
                            command.command_id,
                            lease_id=message["lease_id"],
                            connection_id=connection_id,
                        )
                        await websocket.send_json({"type": "command.state.changed", "command": _command_payload(command)})
                    else:
                        await websocket.send_json({"type": "control.error", "code": "unsupported_operation"})
                except KeyError as exc:
                    await websocket.send_json({"type": "control.error", "code": "invalid_request", "field": str(exc)})
                except CapabilityUnavailable as exc:
                    await websocket.send_json(
                        {
                            "type": "control.error",
                            "code": exc.code,
                            "reason": exc.reason,
                            "required_capability": exc.required_capability,
                        }
                    )
                except (ConflictError, NotFoundError, ValidationError) as exc:
                    await websocket.send_json({"type": "control.error", "code": "rejected", "reason": str(exc)})
        except WebSocketDisconnect:
            await bridge.close_control_connection_safely(connection_id)
        except (ValueError, TypeError):
            await bridge.close_control_connection_safely(connection_id)
            await websocket.close(code=1003)

    web_root = Path(__file__).resolve().parents[1] / "web"
    built_root = web_root / "dist"
    static_root = built_root if built_root.is_dir() else web_root
    if static_root.is_dir():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="web")

    return app
