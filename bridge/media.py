"""Ephemeral, bounded camera-frame assembly and streaming state.

Camera bytes are deliberately kept out of SQLite, the audit log, the Bridge
event log, and the cognitive/provider path.  The store holds at most the latest
complete frame for each device plus one in-flight frame assembly.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import threading
import time
from dataclasses import dataclass
from typing import Any


MAX_CAMERA_FRAME_BYTES = 64 * 1024
MAX_CAMERA_CHUNK_BYTES = 5 * 1024
MAX_CAMERA_CHUNKS = 32
MAX_CAMERA_FRAME_AGE_S = 3.0
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


class CameraFrameError(ValueError):
    """A device frame failed the bounded media contract."""


@dataclass(frozen=True)
class CameraFrame:
    device_id: str
    session_id: str
    frame_id: str
    width: int
    height: int
    size: int
    timestamp_ms: int
    data: bytes


@dataclass
class _Assembly:
    device_id: str
    session_id: str
    frame_id: str
    width: int
    height: int
    size: int
    chunk_count: int
    timestamp_ms: int
    started_at: float
    chunks: dict[int, bytes]


class CameraFrameStore:
    """Assemble and publish bounded JPEG frames without persistence."""

    def __init__(
        self,
        *,
        max_frame_bytes: int = MAX_CAMERA_FRAME_BYTES,
        max_chunk_bytes: int = MAX_CAMERA_CHUNK_BYTES,
        max_chunks: int = MAX_CAMERA_CHUNKS,
        max_frame_age_s: float = MAX_CAMERA_FRAME_AGE_S,
    ) -> None:
        self.max_frame_bytes = max_frame_bytes
        self.max_chunk_bytes = max_chunk_bytes
        self.max_chunks = max_chunks
        self.max_frame_age_s = max_frame_age_s
        self._latest: dict[str, CameraFrame] = {}
        self._assemblies: dict[tuple[str, str, str], _Assembly] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @staticmethod
    def _check_frame_id(frame_id: Any) -> str:
        if not isinstance(frame_id, str) or FRAME_ID_RE.fullmatch(frame_id) is None:
            raise CameraFrameError("invalid frame_id")
        return frame_id

    def begin(
        self,
        *,
        device_id: str,
        session_id: str,
        frame_id: str,
        width: int,
        height: int,
        size: int,
        chunk_count: int,
        timestamp_ms: int,
    ) -> None:
        frame_id = self._check_frame_id(frame_id)
        if width != 320 or height != 240:
            raise CameraFrameError("unsupported camera geometry")
        if not isinstance(size, int) or isinstance(size, bool) or not 2 <= size <= self.max_frame_bytes:
            raise CameraFrameError("camera frame size is outside the bounded limit")
        if (
            not isinstance(chunk_count, int)
            or isinstance(chunk_count, bool)
            or not 1 <= chunk_count <= self.max_chunks
        ):
            raise CameraFrameError("camera chunk count is outside the bounded limit")
        if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool) or timestamp_ms < 0:
            raise CameraFrameError("invalid camera timestamp")
        key = (device_id, session_id, frame_id)
        now = time.monotonic()
        with self._lock:
            self._expire_locked(now)
            # USB media writes are deliberately bounded, so a congested link can
            # lose the rest of one frame after its begin event. A later begin is
            # a resynchronization boundary: discard only the unpublished partial
            # frame so the next complete JPEG can restore liveness.
            stale_keys = [
                assembly_key
                for assembly_key, assembly in self._assemblies.items()
                if assembly.device_id == device_id
            ]
            for stale_key in stale_keys:
                self._assemblies.pop(stale_key, None)
            self._assemblies[key] = _Assembly(
                device_id=device_id,
                session_id=session_id,
                frame_id=frame_id,
                width=width,
                height=height,
                size=size,
                chunk_count=chunk_count,
                timestamp_ms=timestamp_ms,
                started_at=now,
                chunks={},
            )

    def add_chunk(
        self,
        *,
        device_id: str,
        session_id: str,
        frame_id: str,
        index: int,
        chunk_count: int,
        data: str,
    ) -> None:
        frame_id = self._check_frame_id(frame_id)
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or not isinstance(chunk_count, int)
            or isinstance(chunk_count, bool)
        ):
            raise CameraFrameError("invalid camera chunk index")
        if not isinstance(data, str) or not data:
            raise CameraFrameError("camera chunk data is required")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CameraFrameError("camera chunk is not valid base64") from exc
        if not decoded or len(decoded) > self.max_chunk_bytes:
            raise CameraFrameError("camera chunk exceeds the bounded limit")
        key = (device_id, session_id, frame_id)
        with self._lock:
            self._expire_locked(time.monotonic())
            assembly = self._assemblies.get(key)
            if assembly is None:
                raise CameraFrameError("camera frame begin is missing or expired")
            if chunk_count != assembly.chunk_count or not 0 <= index < assembly.chunk_count:
                raise CameraFrameError("camera chunk index/count mismatch")
            if index in assembly.chunks:
                raise CameraFrameError("duplicate camera chunk")
            if sum(len(chunk) for chunk in assembly.chunks.values()) + len(decoded) > assembly.size:
                raise CameraFrameError("camera chunks exceed declared frame size")
            assembly.chunks[index] = decoded

    async def finish(
        self,
        *,
        device_id: str,
        session_id: str,
        frame_id: str,
        size: int,
    ) -> CameraFrame:
        frame_id = self._check_frame_id(frame_id)
        key = (device_id, session_id, frame_id)
        with self._lock:
            assembly = self._assemblies.pop(key, None)
            if assembly is None:
                raise CameraFrameError("camera frame begin is missing or expired")
            if size != assembly.size or len(assembly.chunks) != assembly.chunk_count:
                raise CameraFrameError("camera frame is incomplete")
            data = b"".join(assembly.chunks[index] for index in range(assembly.chunk_count))
            if len(data) != assembly.size or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                raise CameraFrameError("camera frame is not a complete JPEG")
            frame = CameraFrame(
                device_id=device_id,
                session_id=session_id,
                frame_id=frame_id,
                width=assembly.width,
                height=assembly.height,
                size=len(data),
                timestamp_ms=assembly.timestamp_ms,
                data=data,
            )
        with self._condition:
            self._latest[device_id] = frame
            self._condition.notify_all()
        return frame

    def latest(self, device_id: str, *, session_id: str | None = None) -> CameraFrame | None:
        with self._lock:
            frame = self._latest.get(device_id)
            if frame is None or (session_id is not None and frame.session_id != session_id):
                return None
            return frame

    async def wait_for(
        self,
        device_id: str,
        *,
        session_id: str,
        after_frame_id: str | None = None,
        timeout_s: float = 3.0,
    ) -> CameraFrame | None:
        def current() -> CameraFrame | None:
            frame = self.latest(device_id, session_id=session_id)
            if frame is None or frame.frame_id == after_frame_id:
                return None
            return frame

        frame = current()
        if frame is not None:
            return frame
        deadline = time.monotonic() + max(0.05, timeout_s)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            await asyncio.to_thread(
                self._wait_for_change,
                device_id,
                session_id,
                after_frame_id,
                remaining,
            )
            frame = current()
            if frame is not None:
                return frame
        return current()

    def _wait_for_change(
        self,
        device_id: str,
        session_id: str,
        after_frame_id: str | None,
        timeout_s: float,
    ) -> None:
        """Block off-loop until this stream has a newer frame or is invalidated."""

        with self._condition:
            frame = self._latest.get(device_id)
            if (
                frame is not None
                and frame.session_id == session_id
                and frame.frame_id != after_frame_id
            ):
                return
            self._condition.wait(timeout=max(0.0, timeout_s))

    def invalidate_session(self, device_id: str, session_id: str) -> None:
        with self._condition:
            self._assemblies = {
                key: value
                for key, value in self._assemblies.items()
                if not (value.device_id == device_id and value.session_id == session_id)
            }
            frame = self._latest.get(device_id)
            if frame is not None and frame.session_id == session_id:
                self._latest.pop(device_id, None)
            self._condition.notify_all()

    def expire_stale(self) -> int:
        with self._lock:
            return self._expire_locked(time.monotonic())

    def _expire_locked(self, now: float) -> int:
        stale = [
            key
            for key, assembly in self._assemblies.items()
            if now - assembly.started_at > self.max_frame_age_s
        ]
        for key in stale:
            self._assemblies.pop(key, None)
        return len(stale)
