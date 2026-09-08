"""Host-only Edge protocol contract and deterministic fake transport.

This module intentionally has no sockets, Wi-Fi, TLS, or network dependencies.  It is
an adapter seam for a later Edge implementation, not a network implementation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from .errors import TransportError, ValidationError
from .transports.base import ReceiveCallback

EDGE_SCHEMA = "lifeos.edge.v1"
MAX_FRAME_BYTES = 16 * 1024
HEARTBEAT_INTERVAL_MS = 15_000
HEARTBEAT_TIMEOUT_MS = 45_000
MAX_ACTION_TTL_MS = 1_500
MAX_SEQUENCE = 2**63 - 1
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


class EdgeState(str, Enum):
    NEGOTIATING = "negotiating"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    REJECTED = "rejected"


def validate_edge_envelope(frame: dict[str, Any]) -> None:
    """Validate the invariant subset shared by every Edge adapter."""
    if not isinstance(frame, dict):
        raise ValidationError("Edge envelope must be an object")
    required = {"schema", "kind", "type", "message_id", "sender", "edge_id", "device_id", "session_id", "seq", "ts_ms", "payload"}
    if set(frame) - required - {"command_id", "ack_seq", "expires_at_ms", "nonce"}:
        raise ValidationError("Edge envelope contains unknown fields")
    if frame.get("schema") != EDGE_SCHEMA:
        raise ValidationError("unsupported Edge schema")
    if frame.get("kind") not in {"hello", "heartbeat", "command", "ack", "event", "error"}:
        raise ValidationError("unsupported Edge envelope kind")
    sender = frame.get("sender")
    if not isinstance(sender, dict) or set(sender) != {"kind", "id"} or sender["kind"] not in {"edge", "control_plane"}:
        raise ValidationError("invalid Edge sender identity")
    if not isinstance(sender["id"], str) or not 1 <= len(sender["id"]) <= 64:
        raise ValidationError("invalid Edge sender id")
    for key in ("message_id", "edge_id", "device_id", "session_id"):
        if not isinstance(frame.get(key), str) or not frame[key]:
            raise ValidationError(f"invalid Edge {key}")
    if len(frame["message_id"]) > 96 or len(frame["session_id"]) > 96:
        raise ValidationError("Edge message or session id is too long")
    if len(frame["edge_id"]) > 64 or len(frame["device_id"]) > 64:
        raise ValidationError("Edge identity is too long")
    if not isinstance(frame.get("type"), str) or len(frame["type"]) > 64 or _TYPE_RE.fullmatch(frame["type"]) is None:
        raise ValidationError("invalid Edge message type")
    if not isinstance(frame.get("seq"), int) or isinstance(frame["seq"], bool) or not 0 <= frame["seq"] <= MAX_SEQUENCE:
        raise ValidationError("invalid Edge seq")
    if not isinstance(frame.get("ts_ms"), int) or isinstance(frame["ts_ms"], bool) or not 0 <= frame["ts_ms"] <= MAX_SEQUENCE:
        raise ValidationError("invalid Edge timestamp")
    if not isinstance(frame.get("payload"), dict):
        raise ValidationError("Edge payload must be an object")
    if "nonce" in frame and frame["kind"] != "hello":
        raise ValidationError("nonce is hello-only")
    if "command_id" in frame and (
        not isinstance(frame["command_id"], str) or not 1 <= len(frame["command_id"]) <= 96
    ):
        raise ValidationError("invalid Edge command id")
    for key in ("ack_seq", "expires_at_ms"):
        if key in frame and (
            not isinstance(frame[key], int)
            or isinstance(frame[key], bool)
            or not 0 <= frame[key] <= MAX_SEQUENCE
        ):
            raise ValidationError(f"invalid Edge {key}")
    if "nonce" in frame and (
        not isinstance(frame["nonce"], str) or not 16 <= len(frame["nonce"]) <= 128
    ):
        raise ValidationError("invalid Edge nonce")
    if frame["kind"] == "command" and (not isinstance(frame.get("command_id"), str) or not frame["command_id"]):
        raise ValidationError("command requires command_id")
    try:
        raw = json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Edge envelope must contain finite JSON values") from exc
    if len(raw) > MAX_FRAME_BYTES:
        raise ValidationError("Edge frame exceeds 16 KiB")


@dataclass(frozen=True)
class EdgeCommand:
    command_id: str
    session_id: str
    action: str
    expires_at_ms: int


class FakeEdgeTransport:
    """Deterministic in-memory Edge link with explicit fault injection."""

    def __init__(self, *, edge_id: str = "edge-fake-01", device_id: str = "stackchan-fake-01", auto_hello: bool = True) -> None:
        self.edge_id, self.device_id = edge_id, device_id
        self.transport_id = f"fake-edge-{edge_id}"
        self.auto_hello = auto_hello
        self.connected = False
        self.sent_frames: list[dict[str, Any]] = []
        self._receiver: ReceiveCallback | None = None
        self._duplicate_next = False
        self._reorder_buffer: dict[str, Any] | None = None
        self._expire_next = False
        self.backpressured = False

    async def open(self, receiver: ReceiveCallback) -> None:
        self.connected, self._receiver = True, receiver

    async def close(self) -> None:
        self.connected, self._receiver = False, None

    async def send(self, envelope: dict[str, Any]) -> None:
        if not self.connected:
            raise TransportError("fake Edge is disconnected")
        validate_edge_envelope(envelope)
        if self.backpressured:
            raise TransportError("fake Edge backpressure")
        sent = dict(envelope)
        if self._expire_next and "expires_at_ms" in sent:
            sent["expires_at_ms"] = sent["ts_ms"] - 1
            self._expire_next = False
        self.sent_frames.append(sent)

    async def send_priority(self, envelope: dict[str, Any]) -> None:
        await self.send(envelope)

    def inject_disconnect(self) -> None:
        self.connected = False

    def inject_duplicate(self) -> None:
        self._duplicate_next = True

    def inject_reorder(self) -> None:
        self._reorder_buffer = {}

    def inject_expired(self) -> None:
        self._expire_next = True

    async def emit(self, frame: dict[str, Any]) -> None:
        if not self.connected or self._receiver is None:
            raise TransportError("fake Edge is disconnected")
        validate_edge_envelope(frame)
        frames = [frame]
        if self._reorder_buffer is not None:
            if not self._reorder_buffer:
                self._reorder_buffer = dict(frame)
                return
            frames = [frame, self._reorder_buffer]
            self._reorder_buffer = None
        if self._duplicate_next:
            frames *= 2
            self._duplicate_next = False
        for item in frames:
            await self._receiver(dict(item))


class EdgeSession:
    """Single device/session binding; reconnect creates a fresh sequence domain."""

    def __init__(self, transport: FakeEdgeTransport, *, control_plane_id: str = "bridge-fake-01") -> None:
        self.transport = transport
        self.control_plane_id = control_plane_id
        self.state = EdgeState.OFFLINE
        self.session_id: str | None = None
        self.host_seq = -1
        self.edge_seq: int | None = None
        self._fake_edge_seq = -1
        self.last_heartbeat_ms: int | None = None
        self.events: list[dict[str, Any]] = []
        self.commands: dict[str, EdgeCommand] = {}
        self._seen_message_ids: list[str] = []
        self._session_counter = 0

    async def connect(self, *, now_ms: int = 0) -> None:
        self._session_counter += 1
        self.session_id = f"{self.transport.edge_id}-session-{self._session_counter}"
        self.host_seq, self.edge_seq, self._fake_edge_seq = -1, None, -1
        self._seen_message_ids.clear()
        self.state = EdgeState.NEGOTIATING
        await self.transport.open(self.receive)
        await self.transport.send(self._hello(now_ms, sender_kind="control_plane"))
        if self.transport.auto_hello:
            await self.transport.emit(self._hello(now_ms, sender_kind="edge"))

    async def reconnect(self, *, now_ms: int = 0) -> None:
        await self.transport.close()
        await self.connect(now_ms=now_ms)

    async def disconnect(self) -> None:
        await self.transport.close()
        self.state = EdgeState.OFFLINE

    async def send_remote_action(self, action: str, *, now_ms: int, ttl_ms: int = MAX_ACTION_TTL_MS, params: dict[str, Any] | None = None) -> EdgeCommand:
        if self.state is not EdgeState.ONLINE or self.session_id is None:
            raise TransportError("remote actions are rejected while Edge is offline/degraded")
        if not isinstance(action, str) or not 1 <= len(action) <= 64 or action.startswith("command."):
            raise ValidationError("remote action must be a high-level action name")
        if params is not None and not isinstance(params, dict):
            raise ValidationError("remote action params must be an object")
        forbidden = {"schema", "kind", "type", "event_id", "message_id", "seq", "ts_ms", "payload", "yaw_deg", "pitch_deg", "pwm", "gpio", "i2c"}
        if params and any(key in forbidden for key in _nested_keys(params)):
            raise ValidationError("raw wire or hardware fields are not accepted")
        if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
            raise ValidationError("now_ms must be a non-negative integer")
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 1 <= ttl_ms <= MAX_ACTION_TTL_MS:
            raise ValidationError(f"action ttl_ms must be between 1 and {MAX_ACTION_TTL_MS}")
        expires = now_ms + ttl_ms
        if expires <= now_ms:
            raise ValidationError("invalid action TTL")
        command = EdgeCommand(str(uuid4()), self.session_id, action, expires)
        frame = self._frame("command", "command.action", now_ms, {"action": action, "params": params or {}}, command_id=command.command_id, expires_at_ms=expires)
        await self.transport.send(frame)
        self.commands[command.command_id] = command
        return command

    async def receive(self, frame: dict[str, Any]) -> None:
        validate_edge_envelope(frame)
        if frame["sender"] != {"kind": "edge", "id": self.transport.edge_id} or frame["edge_id"] != self.transport.edge_id or frame["device_id"] != self.transport.device_id or frame["session_id"] != self.session_id:
            self.state = EdgeState.REJECTED
            return
        if frame["message_id"] in self._seen_message_ids:
            self.events.append({"type": "edge.message_rejected", "reason": "duplicate", "message_id": frame["message_id"]})
            return
        expected = 0 if self.edge_seq is None else self.edge_seq + 1
        if frame["seq"] != expected:
            self.events.append({"type": "edge.sequence_rejected", "seq": frame["seq"]})
            return
        self._seen_message_ids.append(frame["message_id"])
        del self._seen_message_ids[:-32]
        self.edge_seq = frame["seq"]
        if frame["kind"] == "hello":
            self.state = EdgeState.ONLINE
            self.last_heartbeat_ms = frame["ts_ms"]
        elif frame["kind"] == "heartbeat":
            self.last_heartbeat_ms = frame["ts_ms"]
            self.state = EdgeState.ONLINE
        self.events.append({"type": f"edge.{frame['kind']}", "seq": frame["seq"]})

    def tick(self, *, now_ms: int) -> EdgeState:
        if self.last_heartbeat_ms is not None:
            age = now_ms - self.last_heartbeat_ms
            if age > HEARTBEAT_TIMEOUT_MS:
                self.state = EdgeState.OFFLINE
            elif age > HEARTBEAT_INTERVAL_MS:
                self.state = EdgeState.DEGRADED
        return self.state

    def _hello(self, now_ms: int, *, sender_kind: str) -> dict[str, Any]:
        type_ = "hello.edge" if sender_kind == "edge" else "hello.host"
        nonce = "0123456789abcdef" if sender_kind == "edge" else None
        return self._frame("hello", type_, now_ms, {"protocol": EDGE_SCHEMA}, sender_kind=sender_kind, nonce=nonce)

    def _frame(self, kind: str, type_: str, now_ms: int, payload: dict[str, Any], *, sender_kind: str = "control_plane", command_id: str | None = None, expires_at_ms: int | None = None, nonce: str | None = None) -> dict[str, Any]:
        if sender_kind == "edge":
            self._fake_edge_seq += 1
            seq = self._fake_edge_seq
        else:
            self.host_seq += 1
            seq = self.host_seq
        frame: dict[str, Any] = {"schema": EDGE_SCHEMA, "kind": kind, "type": type_, "message_id": str(uuid4()), "sender": {"kind": sender_kind, "id": self.control_plane_id if sender_kind == "control_plane" else self.transport.edge_id}, "edge_id": self.transport.edge_id, "device_id": self.transport.device_id, "session_id": self.session_id, "seq": self.host_seq, "ts_ms": now_ms, "payload": payload}
        frame["seq"] = seq
        if command_id is not None:
            frame["command_id"] = command_id
        if expires_at_ms is not None:
            frame["expires_at_ms"] = expires_at_ms
        if nonce is not None:
            frame["nonce"] = nonce
        return frame


def _nested_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _nested_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_keys(child)
