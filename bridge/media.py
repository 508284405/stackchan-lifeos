"""Ephemeral, bounded camera-frame assembly and streaming state.

Camera bytes are deliberately kept out of SQLite, the audit log, the Bridge
event log, and the cognitive/provider path.  The store holds at most the latest
complete frame for each device plus one in-flight frame assembly.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


MAX_CAMERA_FRAME_BYTES = 64 * 1024
MAX_CAMERA_CHUNK_BYTES = 5 * 1024
MAX_CAMERA_CHUNKS = 32
MAX_CAMERA_FRAME_AGE_S = 3.0
MAX_MANUAL_CAPTURE_AGE_MS = 500
# The ESP32 and host both use monotonic millisecond clocks, but they are not
# frequency locked.  Keep a conservative allowance between round-trip clock
# samples instead of treating the clocks as identical.
MAX_CLOCK_DRIFT_PPM = 500
MAX_CLOCK_SAMPLE_AGE_MS = 5_000
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
CLOCK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


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
    capture_timestamp_ms: int
    capture_clock_id: str
    received_at_ms: int
    token: str
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
    capture_timestamp_ms: int
    capture_clock_id: str
    received_at_ms: int
    started_at: float
    chunks: dict[int, bytes]


@dataclass(frozen=True)
class ClockEstimate:
    """A bounded mapping from one device boot clock to the host clock.

    For a device timestamp produced between a host request send and response
    receive, the true ``host_ms - device_ms`` offset lies inside
    ``[offset_min_ms, offset_max_ms]``.  Capture age always uses the endpoint
    that produces the oldest possible frame.
    """

    clock_id: str
    offset_min_ms: int
    offset_max_ms: int
    sampled_host_ms: int

    def capture_age_upper_ms(self, *, host_now_ms: int, capture_timestamp_ms: int) -> int | None:
        if host_now_ms < self.sampled_host_ms or capture_timestamp_ms < 0:
            return None
        elapsed_ms = host_now_ms - self.sampled_host_ms
        if elapsed_ms > MAX_CLOCK_SAMPLE_AGE_MS:
            return None
        drift_ms = math.ceil(elapsed_ms * MAX_CLOCK_DRIFT_PPM / 1_000_000) + 1
        oldest_device_now = host_now_ms - (self.offset_min_ms - drift_ms)
        if capture_timestamp_ms > oldest_device_now:
            return None
        return oldest_device_now - capture_timestamp_ms


class SessionClockMapper:
    """Maintain fail-closed round-trip clock bounds for active sessions."""

    def __init__(self) -> None:
        self._estimates: dict[str, ClockEstimate] = {}

    @staticmethod
    def validate_clock_id(clock_id: Any) -> str:
        if not isinstance(clock_id, str) or CLOCK_ID_RE.fullmatch(clock_id) is None:
            raise CameraFrameError("invalid capture clock id")
        return clock_id

    def observe_round_trip(
        self,
        *,
        session_id: str,
        clock_id: str,
        host_sent_ms: int,
        host_received_ms: int,
        device_sent_ms: int,
    ) -> ClockEstimate:
        clock_id = self.validate_clock_id(clock_id)
        values = (host_sent_ms, host_received_ms, device_sent_ms)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise CameraFrameError("invalid clock sample")
        if host_received_ms < host_sent_ms:
            raise CameraFrameError("clock sample host interval is reversed")
        estimate = ClockEstimate(
            clock_id=clock_id,
            offset_min_ms=host_sent_ms - device_sent_ms,
            offset_max_ms=host_received_ms - device_sent_ms,
            sampled_host_ms=host_received_ms,
        )
        self._estimates[session_id] = estimate
        return estimate

    def capture_age_upper_ms(
        self,
        *,
        session_id: str,
        clock_id: str,
        capture_timestamp_ms: int,
        host_now_ms: int,
    ) -> int | None:
        estimate = self._estimates.get(session_id)
        if estimate is None or estimate.clock_id != clock_id:
            return None
        return estimate.capture_age_upper_ms(
            host_now_ms=host_now_ms,
            capture_timestamp_ms=capture_timestamp_ms,
        )

    def invalidate_session(self, session_id: str) -> None:
        self._estimates.pop(session_id, None)


@dataclass
class CameraViewer:
    viewer_id: str
    device_id: str
    session_id: str
    opened_at_ms: int
    last_seen_at_ms: int
    delivered_frame_id: str | None = None
    delivered_token: str | None = None
    delivered_capture_timestamp_ms: int | None = None
    delivered_capture_clock_id: str | None = None
    displayed_frame_id: str | None = None
    displayed_token: str | None = None
    displayed_capture_timestamp_ms: int | None = None
    displayed_capture_clock_id: str | None = None
    displayed_at_ms: int | None = None
    visible: bool = False


class CameraViewerRegistry:
    """Ephemeral viewer/display acknowledgements bound to a control socket."""

    def __init__(self) -> None:
        self._viewers: dict[str, CameraViewer] = {}

    def open(
        self,
        *,
        viewer_id: str,
        device_id: str,
        session_id: str,
        now_ms: int,
    ) -> CameraViewer:
        if not isinstance(viewer_id, str) or FRAME_ID_RE.fullmatch(viewer_id) is None:
            raise CameraFrameError("invalid viewer id")
        current = self._viewers.get(viewer_id)
        if current is not None and (
            current.device_id != device_id or current.session_id != session_id
        ):
            raise CameraFrameError("viewer is already bound to another device session")
        viewer = current or CameraViewer(
            viewer_id=viewer_id,
            device_id=device_id,
            session_id=session_id,
            opened_at_ms=now_ms,
            last_seen_at_ms=now_ms,
        )
        viewer.last_seen_at_ms = now_ms
        self._viewers[viewer_id] = viewer
        return viewer

    def close(self, viewer_id: str) -> CameraViewer | None:
        return self._viewers.pop(viewer_id, None)

    def invalidate_session(self, session_id: str) -> list[CameraViewer]:
        removed = [viewer for viewer in self._viewers.values() if viewer.session_id == session_id]
        for viewer in removed:
            self._viewers.pop(viewer.viewer_id, None)
        return removed

    def get(self, viewer_id: str) -> CameraViewer | None:
        return self._viewers.get(viewer_id)

    def list(self) -> list[CameraViewer]:
        return list(self._viewers.values())

    def deliver(self, viewer_id: str, frame: CameraFrame, *, now_ms: int) -> CameraViewer:
        viewer = self._viewers.get(viewer_id)
        if viewer is None or viewer.device_id != frame.device_id or viewer.session_id != frame.session_id:
            raise CameraFrameError("viewer is not bound to this camera session")
        viewer.last_seen_at_ms = now_ms
        viewer.delivered_frame_id = frame.frame_id
        viewer.delivered_token = frame.token
        viewer.delivered_capture_timestamp_ms = frame.capture_timestamp_ms
        viewer.delivered_capture_clock_id = frame.capture_clock_id
        return viewer

    def acknowledge_display(
        self,
        *,
        viewer_id: str,
        device_id: str,
        session_id: str,
        frame_id: str,
        token: str,
        visible: bool,
        now_ms: int,
    ) -> CameraViewer:
        viewer = self._viewers.get(viewer_id)
        if viewer is None or viewer.device_id != device_id or viewer.session_id != session_id:
            raise CameraFrameError("viewer is not bound to this camera session")
        if visible is not True:
            raise CameraFrameError("viewer is not visible")
        if (
            viewer.delivered_frame_id != frame_id
            or viewer.delivered_token != token
        ):
            raise CameraFrameError("display acknowledgement token does not match the served frame")
        if viewer.displayed_frame_id == frame_id and viewer.displayed_token == token:
            raise CameraFrameError("duplicate frame cannot refresh display freshness")
        viewer.last_seen_at_ms = now_ms
        viewer.displayed_frame_id = frame_id
        viewer.displayed_token = token
        viewer.displayed_capture_timestamp_ms = viewer.delivered_capture_timestamp_ms
        viewer.displayed_capture_clock_id = viewer.delivered_capture_clock_id
        viewer.displayed_at_ms = now_ms
        viewer.visible = True
        return viewer

    def active_for_device(self, device_id: str, session_id: str) -> list[CameraViewer]:
        return [
            viewer
            for viewer in self._viewers.values()
            if viewer.device_id == device_id and viewer.session_id == session_id
        ]


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
        capture_timestamp_ms: int | None = None,
        capture_clock_id: str = "legacy",
        received_at_ms: int | None = None,
        timestamp_ms: int | None = None,
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
        if capture_timestamp_ms is None:
            capture_timestamp_ms = timestamp_ms
        if received_at_ms is None:
            received_at_ms = capture_timestamp_ms
        capture_clock_id = SessionClockMapper.validate_clock_id(capture_clock_id)
        if (
            not isinstance(capture_timestamp_ms, int)
            or isinstance(capture_timestamp_ms, bool)
            or capture_timestamp_ms < 0
            or not isinstance(received_at_ms, int)
            or isinstance(received_at_ms, bool)
            or received_at_ms < 0
        ):
            raise CameraFrameError("invalid camera capture timestamp")
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
                capture_timestamp_ms=capture_timestamp_ms,
                capture_clock_id=capture_clock_id,
                received_at_ms=received_at_ms,
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
                capture_timestamp_ms=assembly.capture_timestamp_ms,
                capture_clock_id=assembly.capture_clock_id,
                received_at_ms=assembly.received_at_ms,
                token=secrets.token_urlsafe(18),
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
