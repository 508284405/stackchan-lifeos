"""Serializable records used by the Web Bridge repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .enums import (
    AuditKind,
    BatchState,
    BatchTargetState,
    CommandSource,
    CommandState,
    DeviceLifecycle,
    LeaseState,
    SessionState,
    MaintenanceTaskState,
    RolloutTaskState,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _time_value(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class DiscoveryCandidate:
    candidate_id: str
    hardware_id: str
    device_id: str
    transport_id: str
    firmware_version: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    discovered_at: datetime = field(default_factory=utc_now)


@dataclass
class DeviceRecord:
    device_id: str
    hardware_id: str
    display_name: str
    site_id: str = "local"
    transport_hint: str | None = None
    firmware_version: str | None = None
    protocol_version: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    desired_config_version: str | None = None
    last_seen_at: datetime | None = None
    lifecycle_state: DeviceLifecycle = DeviceLifecycle.REGISTERED

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "hardware_id": self.hardware_id,
            "display_name": self.display_name,
            "site_id": self.site_id,
            "transport_hint": self.transport_hint,
            "firmware_version": self.firmware_version,
            "protocol_version": self.protocol_version,
            "capabilities": sorted(self.capabilities),
            "desired_config_version": self.desired_config_version,
            "last_seen_at": _time_value(self.last_seen_at),
            "lifecycle_state": self.lifecycle_state.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceRecord":
        return cls(
            device_id=data["device_id"],
            hardware_id=data["hardware_id"],
            display_name=data["display_name"],
            site_id=data.get("site_id", "local"),
            transport_hint=data.get("transport_hint"),
            firmware_version=data.get("firmware_version"),
            protocol_version=data.get("protocol_version"),
            capabilities=frozenset(data.get("capabilities", [])),
            desired_config_version=data.get("desired_config_version"),
            last_seen_at=_parse_time(data.get("last_seen_at")),
            lifecycle_state=DeviceLifecycle(data.get("lifecycle_state", DeviceLifecycle.REGISTERED)),
        )


@dataclass
class DeviceSession:
    session_id: str
    device_id: str
    edge_id: str
    transport_id: str
    nonce: str
    state: SessionState = SessionState.NEGOTIATING
    connected_at: datetime = field(default_factory=utc_now)
    last_heartbeat_at: datetime | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    rx_seq: int | None = None
    tx_seq: int = -1
    active_control_lease: str | None = None
    seen_event_ids: list[str] = field(default_factory=list)
    host_hello_sent: bool = False

    def to_dict(self, *, include_nonce: bool = False) -> dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "edge_id": self.edge_id,
            "transport_id": self.transport_id,
            "state": self.state.value,
            "connected_at": _time_value(self.connected_at),
            "last_heartbeat_at": _time_value(self.last_heartbeat_at),
            "capabilities": sorted(self.capabilities),
            "rx_seq": self.rx_seq,
            "tx_seq": self.tx_seq,
            "active_control_lease": self.active_control_lease,
            "seen_event_ids": list(self.seen_event_ids),
            "host_hello_sent": self.host_hello_sent,
        }
        if include_nonce:
            data["nonce"] = self.nonce
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceSession":
        return cls(
            session_id=data["session_id"],
            device_id=data["device_id"],
            edge_id=data.get("edge_id", "local"),
            transport_id=data["transport_id"],
            nonce=data.get("nonce", ""),
            state=SessionState(data.get("state", SessionState.NEGOTIATING)),
            connected_at=_parse_time(data.get("connected_at")) or utc_now(),
            last_heartbeat_at=_parse_time(data.get("last_heartbeat_at")),
            capabilities=frozenset(data.get("capabilities", [])),
            rx_seq=data.get("rx_seq"),
            tx_seq=data.get("tx_seq", -1),
            active_control_lease=data.get("active_control_lease"),
            seen_event_ids=list(data.get("seen_event_ids", [])),
            host_hello_sent=bool(data.get("host_hello_sent", False)),
        )


@dataclass
class ControlLease:
    lease_id: str
    device_id: str
    session_id: str
    connection_id: str
    issued_at: datetime
    expires_at: datetime
    max_expires_at: datetime
    last_input_seq: int = 0
    last_input_at: datetime | None = None
    state: LeaseState = LeaseState.ACTIVE
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "connection_id": self.connection_id,
            "issued_at": _time_value(self.issued_at),
            "expires_at": _time_value(self.expires_at),
            "max_expires_at": _time_value(self.max_expires_at),
            "last_input_seq": self.last_input_seq,
            "last_input_at": _time_value(self.last_input_at),
            "state": self.state.value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlLease":
        return cls(
            lease_id=data["lease_id"],
            device_id=data["device_id"],
            session_id=data["session_id"],
            connection_id=data["connection_id"],
            issued_at=_parse_time(data["issued_at"]) or utc_now(),
            expires_at=_parse_time(data["expires_at"]) or utc_now(),
            max_expires_at=_parse_time(data["max_expires_at"]) or utc_now(),
            last_input_seq=int(data.get("last_input_seq", 0)),
            last_input_at=_parse_time(data.get("last_input_at")),
            state=LeaseState(data.get("state", LeaseState.ACTIVE)),
            reason=data.get("reason"),
        )


@dataclass
class CommandRecord:
    command_id: str
    device_id: str
    session_id: str | None
    source: CommandSource
    type: str
    wire_type: str
    payload: dict[str, Any]
    priority: int
    issued_at: datetime
    expires_at: datetime
    issued_at_ms: int
    expires_at_ms: int
    correlation_id: str
    idempotency_key: str | None = None
    state: CommandState = CommandState.CREATED
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    late_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "source": self.source.value,
            "type": self.type,
            "wire_type": self.wire_type,
            "payload": self.payload,
            "priority": self.priority,
            "issued_at": _time_value(self.issued_at),
            "expires_at": _time_value(self.expires_at),
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "state": self.state.value,
            "result": self.result,
            "error": self.error,
            "late_evidence": self.late_evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandRecord":
        return cls(
            command_id=data["command_id"],
            device_id=data["device_id"],
            session_id=data.get("session_id"),
            source=CommandSource(data["source"]),
            type=data["type"],
            wire_type=data.get("wire_type", data["type"]),
            payload=dict(data.get("payload", {})),
            priority=int(data["priority"]),
            issued_at=_parse_time(data["issued_at"]) or utc_now(),
            expires_at=_parse_time(data["expires_at"]) or utc_now(),
            issued_at_ms=int(data["issued_at_ms"]),
            expires_at_ms=int(data["expires_at_ms"]),
            correlation_id=data["correlation_id"],
            idempotency_key=data.get("idempotency_key"),
            state=CommandState(data.get("state", CommandState.CREATED)),
            result=data.get("result"),
            error=data.get("error"),
            late_evidence=list(data.get("late_evidence", [])),
        )


@dataclass
class BatchTarget:
    device_id: str
    state: BatchTargetState = BatchTargetState.PENDING
    command_id: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "state": self.state.value,
            "command_id": self.command_id,
            "result": self.result,
            "error": self.error,
            "finished_at": _time_value(self.finished_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchTarget":
        return cls(
            device_id=data["device_id"],
            state=BatchTargetState(data.get("state", BatchTargetState.PENDING)),
            command_id=data.get("command_id"),
            result=data.get("result"),
            error=data.get("error"),
            finished_at=_parse_time(data.get("finished_at")),
        )


@dataclass
class BatchTask:
    task_id: str
    command_type: str
    params: dict[str, Any]
    targets: list[BatchTarget]
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    aggregate_state: BatchState = BatchState.PENDING
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command_type": self.command_type,
            "params": self.params,
            "targets": [target.to_dict() for target in self.targets],
            "created_at": _time_value(self.created_at),
            "expires_at": _time_value(self.expires_at),
            "aggregate_state": self.aggregate_state.value,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchTask":
        return cls(
            task_id=data["task_id"],
            command_type=data["command_type"],
            params=dict(data.get("params", {})),
            targets=[BatchTarget.from_dict(item) for item in data.get("targets", [])],
            created_at=_parse_time(data.get("created_at")) or utc_now(),
            expires_at=_parse_time(data.get("expires_at")),
            aggregate_state=BatchState(data.get("aggregate_state", BatchState.PENDING)),
            correlation_id=data.get("correlation_id"),
        )


@dataclass
class AuditRecord:
    audit_id: str = field(default_factory=lambda: str(uuid4()))
    kind: AuditKind = AuditKind.COMMAND_STATE_CHANGED
    occurred_at: datetime = field(default_factory=utc_now)
    device_id: str | None = None
    session_id: str | None = None
    command_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "kind": self.kind.value,
            "occurred_at": _time_value(self.occurred_at),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "command_id": self.command_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditRecord":
        return cls(
            audit_id=data["audit_id"],
            kind=AuditKind(data["kind"]),
            occurred_at=_parse_time(data.get("occurred_at")) or utc_now(),
            device_id=data.get("device_id"),
            session_id=data.get("session_id"),
            command_id=data.get("command_id"),
            correlation_id=data.get("correlation_id"),
            payload=dict(data.get("payload", {})),
        )


@dataclass
class MaintenanceTask:
    task_id: str
    device_id: str
    operation: str
    session_id: str
    challenge_id: str
    state: MaintenanceTaskState = MaintenanceTaskState.AWAITING_CONFIRMATION
    consequence: str = "may change device state"
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "device_id": self.device_id, "operation": self.operation,
                "session_id": self.session_id, "challenge_id": self.challenge_id,
                "state": self.state.value, "consequence": self.consequence, "error": self.error,
                "created_at": _time_value(self.created_at), "updated_at": _time_value(self.updated_at)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MaintenanceTask":
        return cls(task_id=data["task_id"], device_id=data["device_id"], operation=data["operation"],
                   session_id=data["session_id"], challenge_id=data["challenge_id"],
                   state=MaintenanceTaskState(data.get("state", "awaiting_confirmation")),
                   consequence=data.get("consequence", "may change device state"), error=data.get("error"),
                   created_at=_parse_time(data.get("created_at")) or utc_now(),
                   updated_at=_parse_time(data.get("updated_at")) or utc_now())


@dataclass
class RolloutTask:
    task_id: str
    device_id: str
    image_ref: str
    state: RolloutTaskState = RolloutTaskState.PENDING
    consequence: str = "may replace device firmware"
    preconditions: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "device_id": self.device_id, "image_ref": self.image_ref,
                "state": self.state.value, "consequence": self.consequence,
                "preconditions": self.preconditions, "error": self.error,
                "created_at": _time_value(self.created_at), "updated_at": _time_value(self.updated_at)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RolloutTask":
        return cls(task_id=data["task_id"], device_id=data["device_id"], image_ref=data["image_ref"],
                   state=RolloutTaskState(data.get("state", "pending")),
                   consequence=data.get("consequence", "may replace device firmware"),
                   preconditions=dict(data.get("preconditions", {})), error=data.get("error"),
                   created_at=_parse_time(data.get("created_at")) or utc_now(),
                   updated_at=_parse_time(data.get("updated_at")) or utc_now())
