"""Stable Web Bridge state and source enums."""

from enum import Enum


class DeviceLifecycle(str, Enum):
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    REVOKED = "revoked"


class SessionState(str, Enum):
    NEGOTIATING = "negotiating"
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    REJECTED = "rejected"


class CommandState(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    ROUTED = "routed"
    SENT = "sent"
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SAFETY_BLOCKED = "safety_blocked"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    EXPIRED = "expired"
    PREEMPTED = "preempted"
    CANCELLED = "cancelled"


class CommandSource(str, Enum):
    WEB = "web"
    MANUAL = "manual"
    BATCH = "batch"
    AGENT = "agent"
    MAINTENANCE = "maintenance"
    SYSTEM = "system"


class BatchState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAUSED = "paused"


class BatchTargetState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    REJECTED = "rejected"
    OFFLINE = "offline"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FAILED = "failed"
    RECOVERED = "recovered"


class LeaseState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    PREEMPTED = "preempted"
    INVALID = "invalid"


class AuditKind(str, Enum):
    DEVICE_DISCOVERED = "device.discovered"
    DEVICE_CLAIMED = "device.claimed"
    SESSION_CHANGED = "session.changed"
    COMMAND_STATE_CHANGED = "command.state.changed"
    PROTOCOL_REJECTED = "protocol.rejected"
    PROTOCOL_DUPLICATE = "protocol.duplicate"
    MEDIA_SEQUENCE_RESYNC = "media.sequence.resynced"
    COMMAND_LATE_EVIDENCE = "command.late_evidence"
    HEALTH_RECEIVED = "health.received"
    RECOVERY_EXPIRED = "recovery.expired"
    BATCH_STATE_CHANGED = "batch.state.changed"
    CONTROL_LEASE_CHANGED = "control.lease.changed"
    MAINTENANCE_TASK_CHANGED = "maintenance.task.changed"
    MAINTENANCE_CHALLENGE_CHANGED = "maintenance.challenge.changed"
    DIAGNOSTIC_EXPORTED = "diagnostic.exported"
    ROLLOUT_TASK_CHANGED = "rollout.task.changed"


class MaintenanceTaskState(str, Enum):
    PENDING = "pending"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RolloutTaskState(str, Enum):
    PENDING = "pending"
    PREFLIGHT = "preflight"
    READY = "ready"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    RECOVERED = "recovered"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL_COMMAND_STATES = frozenset(
    {
        CommandState.COMPLETED,
        CommandState.REJECTED,
        CommandState.SAFETY_BLOCKED,
        CommandState.OFFLINE,
        CommandState.TIMEOUT,
        CommandState.EXPIRED,
        CommandState.PREEMPTED,
        CommandState.CANCELLED,
    }
)
