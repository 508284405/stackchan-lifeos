"""Bounded Bridge event log with snapshot-plus-cursor recovery semantics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4

from .domain import utc_now


class CursorExpired(Exception):
    """The requested cursor fell outside the retained event window."""

    code = "resync_required"


@dataclass
class BridgeEvent:
    event_id: str = field(default_factory=lambda: f"evt-{uuid4()}")
    type: str = "bridge.event"
    occurred_at: datetime = field(default_factory=utc_now)
    device_id: str | None = None
    session_id: str | None = None
    command_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    cursor: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat(),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "command_id": self.command_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "cursor": str(self.cursor),
        }


class EventLog:
    """Thread-safe, bounded event retention for WebSocket consumers."""

    def __init__(self, *, max_events: int = 512) -> None:
        if not isinstance(max_events, int) or max_events < 8:
            raise ValueError("max_events must be an integer >= 8")
        self.max_events = max_events
        self._events: deque[BridgeEvent] = deque(maxlen=max_events)
        self._next_cursor = 0
        self._lock = RLock()

    @property
    def latest_cursor(self) -> str:
        with self._lock:
            return str(self._next_cursor)

    @staticmethod
    def _payload_size(payload: dict[str, Any]) -> int:
        import json

        try:
            return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("event payload must be finite JSON") from exc

    def append(
        self,
        *,
        type: str,
        payload: dict[str, Any] | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
        correlation_id: str | None = None,
        telemetry_key: str | None = None,
    ) -> BridgeEvent:
        payload = dict(payload or {})
        if self._payload_size(payload) > 8 * 1024:
            raise ValueError("event payload exceeds 8 KiB")
        with self._lock:
            if telemetry_key is not None and self._events:
                last = self._events[-1]
                if (
                    last.type == type
                    and last.device_id == device_id
                    and last.payload.get("_telemetry_key") == telemetry_key
                ):
                    self._next_cursor += 1
                    event = BridgeEvent(
                        event_id=last.event_id,
                        type=type,
                        occurred_at=utc_now(),
                        device_id=device_id,
                        session_id=session_id,
                        command_id=command_id,
                        correlation_id=correlation_id,
                        payload=payload,
                        cursor=self._next_cursor,
                    )
                    self._events[-1] = event
                    return event
            if len(self._events) >= self.max_events and telemetry_key is not None:
                telemetry_index = next(
                    (
                        index
                        for index, existing in enumerate(self._events)
                        if existing.type == "telemetry.sampled"
                    ),
                    None,
                )
                if telemetry_index is None:
                    # Keep command/session/safety events when the bounded log
                    # is full; the audit store remains the durable source.
                    return BridgeEvent(type=type, device_id=device_id, payload=payload)
                del self._events[telemetry_index]
            self._next_cursor += 1
            stored_payload = dict(payload)
            if telemetry_key is not None:
                stored_payload["_telemetry_key"] = telemetry_key
            event = BridgeEvent(
                type=type,
                occurred_at=utc_now(),
                device_id=device_id,
                session_id=session_id,
                command_id=command_id,
                correlation_id=correlation_id,
                payload=stored_payload,
                cursor=self._next_cursor,
            )
            self._events.append(event)
            return event

    def since(
        self,
        cursor: str | int | None = None,
        *,
        device_ids: Iterable[str] | None = None,
        include_telemetry: bool = True,
        limit: int = 100,
    ) -> list[BridgeEvent]:
        if not isinstance(limit, int) or not 0 < limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        try:
            parsed_cursor = 0 if cursor is None else int(cursor)
        except (TypeError, ValueError) as exc:
            raise ValueError("cursor must be an integer") from exc
        if parsed_cursor < 0:
            raise ValueError("cursor must be non-negative")
        selected_devices = set(device_ids) if device_ids is not None else None
        with self._lock:
            oldest_cursor = self._events[0].cursor if self._events else self._next_cursor + 1
            if parsed_cursor and parsed_cursor < oldest_cursor - 1:
                raise CursorExpired("event cursor is outside the retained window")
            result: list[BridgeEvent] = []
            for event in self._events:
                if event.cursor <= parsed_cursor:
                    continue
                if selected_devices is not None and event.device_id not in selected_devices:
                    continue
                if not include_telemetry and event.type == "telemetry.sampled":
                    continue
                payload = dict(event.payload)
                payload.pop("_telemetry_key", None)
                result.append(
                    BridgeEvent(
                        event_id=event.event_id,
                        type=event.type,
                        occurred_at=event.occurred_at,
                        device_id=event.device_id,
                        session_id=event.session_id,
                        command_id=event.command_id,
                        correlation_id=event.correlation_id,
                        payload=payload,
                        cursor=event.cursor,
                    )
                )
                if len(result) >= limit:
                    break
            return result
