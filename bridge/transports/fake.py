"""Programmable fake device/transport for the W1 bridge acceptance loop."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any
from uuid import uuid4

from ..domain import DiscoveryCandidate
from ..errors import TransportError
from .base import ReceiveCallback


_FAKE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    "wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/"
    "9oACAEDAQE/AX//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/AX//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/"
    "2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


class FakeTransport:
    """A deterministic fake StackChan speaking the lifeos.v1 JSONL shape.

    The fake deliberately models ACK admission separately from completion. Test
    cases can drop ACKs, delay responses, reject selected command types, or
    disable completion without changing the bridge's transport interface.
    """

    host_test_only = True

    def __init__(
        self,
        *,
        device_id: str = "stackchan-fake-01",
        hardware_id: str = "fake-hw-01",
        transport_id: str = "fake-usb-01",
        firmware_version: str = "fake-0.1.0",
        firmware_boot_state: str = "legacy",
        firmware_image_sha256: str = "0" * 64,
        capabilities: set[str] | frozenset[str] | None = None,
        hello_mode: str = "device_first",
        auto_ack: bool = True,
        auto_complete: bool = True,
        response_delay_ms: int = 0,
    ) -> None:
        self.device_id = device_id
        self.hardware_id = hardware_id
        self.transport_id = transport_id
        self.firmware_version = firmware_version
        self.firmware_boot_state = firmware_boot_state
        self.firmware_image_sha256 = firmware_image_sha256
        declared_capabilities = set(
            capabilities
            or {
                "status",
                "control",
                "motion",
                "safety",
                "protocol",
                "emergency_stop",
                "touch",
                "imu",
                "display",
                "servo_yaw",
                "servo_pitch",
                "camera",
            }
        )
        if "camera" in declared_capabilities:
            declared_capabilities.add("camera_capture_ts_v1")
        if "manual_control_v1" in declared_capabilities:
            declared_capabilities.add("manual_video_guard_v1")
        self.capabilities = frozenset(declared_capabilities)
        self.auto_ack = auto_ack
        self.auto_complete = auto_complete
        self.response_delay_ms = response_delay_ms
        if hello_mode not in {"device_first", "host_first"}:
            raise ValueError("hello_mode must be device_first or host_first")
        self.hello_mode = hello_mode
        self.hello_host_first = hello_mode == "host_first"
        self.drop_ack = False
        self.fail_send = False
        self.reject_commands: dict[str, str] = {}
        self.sent_frames: list[dict[str, Any]] = []
        self.pending_commands: list[dict[str, Any]] = []
        self.executed_commands: set[str] = set()
        self.execution_count = 0
        self.connected = False
        self._receiver: ReceiveCallback | None = None
        self._device_seq = -1
        self._device_clock_ms = 0
        self.clock_id = f"fake-clock-{uuid4()}"
        self._device_hello_sent = False
        self._camera_task: asyncio.Task[None] | None = None
        self._camera_preview_active = False
        self._camera_fps = 10
        self._camera_frame_number = 0
        self.camera_capture_age_ms = 0
        self.camera_frozen = False
        self.firmware_fail_action: str | None = None
        self._firmware_manifest: dict[str, Any] | None = None
        self._firmware_image = bytearray()

    def candidate(self) -> DiscoveryCandidate:
        return DiscoveryCandidate(
            candidate_id=f"candidate-{self.transport_id}",
            hardware_id=self.hardware_id,
            device_id=self.device_id,
            transport_id=self.transport_id,
            firmware_version=self.firmware_version,
            capabilities=self.capabilities,
        )

    async def open(self, receiver: ReceiveCallback) -> None:
        self.connected = True
        self._receiver = receiver
        self._device_seq = -1
        self._device_clock_ms = 0
        self._device_hello_sent = False
        if self.hello_mode == "device_first":
            await self._emit_device_hello()

    async def close(self) -> None:
        self.connected = False
        self._receiver = None
        self._camera_preview_active = False
        if self._camera_task is not None:
            self._camera_task.cancel()
            await asyncio.gather(self._camera_task, return_exceptions=True)
            self._camera_task = None

    async def send(self, envelope: dict[str, Any]) -> None:
        if not self.connected:
            raise TransportError("fake transport is disconnected")
        if self.fail_send:
            raise TransportError("fake transport injected send failure")
        self.sent_frames.append(dict(envelope))
        if (
            envelope.get("kind") == "hello"
            and envelope.get("type") == "hello.host"
            and self.hello_mode == "host_first"
            and not self._device_hello_sent
        ):
            await self._emit_device_hello(correlation_id=envelope.get("event_id"))
            return
        if envelope.get("kind") == "event" and envelope.get("type") == "host.heartbeat":
            if "health" in self.capabilities:
                await self.emit_heartbeat()
            return
        if envelope.get("kind") != "command":
            return

        command_id = envelope["event_id"]
        if command_id in self.executed_commands:
            if self.auto_ack and not self.drop_ack:
                await self._ack(envelope, "duplicate", idempotent=True)
            return

        self.pending_commands.append(dict(envelope))
        if not self.auto_ack or self.drop_ack:
            return
        if self.response_delay_ms:
            await asyncio.sleep(self.response_delay_ms / 1000)
        rejection = self.reject_commands.get(envelope.get("type"))
        if rejection:
            await self._ack(envelope, "rejected", error_code=rejection)
            return

        self.executed_commands.add(command_id)
        self.execution_count += 1
        if envelope.get("type") == "command.camera_preview":
            action = envelope.get("payload", {}).get("action")
            await self._ack(envelope, "completed")
            if action == "start":
                self._camera_preview_active = True
                fps = envelope.get("payload", {}).get("fps", 10)
                self._camera_fps = max(1, min(10, int(fps))) if isinstance(fps, int) else 10
                if self._camera_task is None or self._camera_task.done():
                    self._camera_task = asyncio.create_task(self._camera_loop())
            else:
                self._camera_preview_active = False
                if self._camera_task is not None:
                    self._camera_task.cancel()
                    await asyncio.gather(self._camera_task, return_exceptions=True)
                    self._camera_task = None
            return
        if envelope.get("type") == "command.firmware_update":
            payload = envelope.get("payload", {})
            action = payload.get("action")
            if self.firmware_fail_action == action:
                await self._ack(envelope, "rejected", error_code="internal")
                return
            if action == "begin":
                self._firmware_manifest = dict(payload)
                self._firmware_image = bytearray()
            elif action == "chunk":
                if self._firmware_manifest is None or payload.get("offset") != len(self._firmware_image):
                    await self._ack(envelope, "rejected", error_code="invalid_schema")
                    return
                try:
                    self._firmware_image.extend(base64.b64decode(payload.get("data", ""), validate=True))
                except (ValueError, TypeError):
                    await self._ack(envelope, "rejected", error_code="invalid_schema")
                    return
            elif action == "commit":
                manifest = self._firmware_manifest or {}
                if (
                    len(self._firmware_image) != manifest.get("size_bytes")
                    or hashlib.sha256(self._firmware_image).hexdigest() != manifest.get("sha256_hex")
                ):
                    await self._ack(envelope, "rejected", error_code="invalid_schema")
                    return
                self.firmware_version = str(manifest.get("version"))
                self.firmware_image_sha256 = str(manifest.get("sha256_hex"))
            else:
                await self._ack(envelope, "rejected", error_code="unsupported")
                return
            await self._ack(envelope, "completed")
            return
        await self._ack(envelope, "accepted")
        if self.auto_complete:
            await self.emit(
                kind="event",
                type="motion.completed",
                correlation_id=command_id,
                payload={
                    "action_id": command_id,
                    "result": "completed",
                    "actual": {},
                },
            )

    async def send_priority(self, envelope: dict[str, Any]) -> None:
        """Emergency path bypasses any caller-side ordinary command lock."""

        await self.send(envelope)

    async def acknowledge_pending(self, command_id: str, status: str = "accepted") -> None:
        envelope = next(
            (item for item in self.pending_commands if item.get("event_id") == command_id),
            None,
        )
        if envelope is None:
            raise KeyError(command_id)
        await self._ack(envelope, status)

    async def emit_heartbeat(self, *, uptime_ms: int = 1_000) -> None:
        await self.emit(
            kind="event",
            type="health.report",
            payload={"heap": 100_000, "uptime_ms": uptime_ms, "faults": []},
        )

    async def _emit_device_hello(self, correlation_id: str | None = None) -> None:
        self._device_hello_sent = True
        await self.emit(
            kind="hello",
            type="hello.device",
            payload={
                "firmware": self.firmware_version,
                "firmware_image_version": self.firmware_version,
                "firmware_boot_state": self.firmware_boot_state,
                "firmware_image_sha256": self.firmware_image_sha256,
                "hardware_id": self.hardware_id,
                "clock_id": self.clock_id,
                "protocol_versions": ["lifeos.v1"],
                "capabilities": sorted(self.capabilities),
                "safety": {"hard_stop": True, "max_command_age_ms": 1500},
            },
            correlation_id=correlation_id,
        )

    async def _camera_loop(self) -> None:
        while self.connected and self._camera_preview_active:
            if self.camera_frozen:
                await asyncio.sleep(1 / self._camera_fps)
                continue
            self._camera_frame_number += 1
            frame_id = f"fake-frame-{self._camera_frame_number}"
            encoded = base64.b64encode(_FAKE_JPEG).decode("ascii")
            capture_ts_ms = max(0, self._device_clock_ms + 1 - self.camera_capture_age_ms)
            await self.emit(
                kind="event",
                type="camera.frame.begin",
                payload={
                    "frame_id": frame_id,
                    "format": "jpeg",
                    "width": 320,
                    "height": 240,
                    "size": len(_FAKE_JPEG),
                    "chunk_count": 1,
                    "capture_ts_ms": capture_ts_ms,
                    "clock_id": self.clock_id,
                },
            )
            await self.emit(
                kind="event",
                type="camera.frame.chunk",
                payload={
                    "frame_id": frame_id,
                    "index": 0,
                    "chunk_count": 1,
                    "data": encoded,
                },
            )
            await self.emit(
                kind="event",
                type="camera.frame.end",
                payload={"frame_id": frame_id, "size": len(_FAKE_JPEG)},
            )
            await asyncio.sleep(1 / self._camera_fps)

    async def emit(
        self,
        *,
        kind: str,
        type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        if not self.connected or self._receiver is None:
            raise TransportError("fake transport is disconnected")
        self._device_seq += 1
        self._device_clock_ms += 1
        frame: dict[str, Any] = {
            "schema": "lifeos.v1",
            "kind": kind,
            "type": type,
            "event_id": f"fake-{uuid4()}",
            "device_id": self.device_id,
            "seq": self._device_seq,
            "ts_ms": self._device_clock_ms,
            "payload": payload,
        }
        if correlation_id is not None:
            frame["correlation_id"] = correlation_id
        await self._receiver(frame)

    async def _ack(
        self,
        envelope: dict[str, Any],
        status: str,
        *,
        idempotent: bool = False,
        error_code: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status, "idempotent": idempotent}
        if error_code:
            payload["error_code"] = error_code
        await self.emit(
            kind="ack",
            type="ack.command",
            correlation_id=envelope["event_id"],
            payload=payload,
        )
