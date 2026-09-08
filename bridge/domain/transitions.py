"""Explicit state-machine transition guards for bridge records."""

from __future__ import annotations

from .enums import BatchState, CommandState, LeaseState, SessionState, MaintenanceTaskState, RolloutTaskState
from ..errors import ConflictError


_COMMAND_TRANSITIONS: dict[CommandState, frozenset[CommandState]] = {
    CommandState.CREATED: frozenset(
        {
            CommandState.VALIDATED,
            CommandState.REJECTED,
            CommandState.OFFLINE,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
    CommandState.VALIDATED: frozenset(
        {
            CommandState.ROUTED,
            CommandState.REJECTED,
            CommandState.OFFLINE,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
    CommandState.ROUTED: frozenset(
        {
            CommandState.SENT,
            CommandState.REJECTED,
            CommandState.OFFLINE,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
    CommandState.SENT: frozenset(
        {
            CommandState.ACCEPTED,
            CommandState.EXECUTING,
            CommandState.COMPLETED,
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.OFFLINE,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
    CommandState.ACCEPTED: frozenset(
        {
            CommandState.EXECUTING,
            CommandState.COMPLETED,
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
    CommandState.EXECUTING: frozenset(
        {
            CommandState.COMPLETED,
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
        }
    ),
}

_SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.NEGOTIATING: frozenset(
        {SessionState.ONLINE, SessionState.REJECTED, SessionState.OFFLINE}
    ),
    SessionState.ONLINE: frozenset(
        {SessionState.DEGRADED, SessionState.OFFLINE, SessionState.MAINTENANCE, SessionState.REJECTED}
    ),
    SessionState.DEGRADED: frozenset(
        {SessionState.ONLINE, SessionState.OFFLINE, SessionState.REJECTED}
    ),
    SessionState.MAINTENANCE: frozenset(
        {SessionState.ONLINE, SessionState.DEGRADED, SessionState.OFFLINE, SessionState.REJECTED}
    ),
    SessionState.OFFLINE: frozenset({SessionState.NEGOTIATING, SessionState.REJECTED}),
    SessionState.REJECTED: frozenset({SessionState.NEGOTIATING, SessionState.OFFLINE}),
}

_BATCH_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.PENDING: frozenset({BatchState.RUNNING, BatchState.CANCELLED, BatchState.EXPIRED}),
    BatchState.RUNNING: frozenset(
        {
            BatchState.COMPLETED,
            BatchState.PARTIAL,
            BatchState.FAILED,
            BatchState.CANCELLED,
            BatchState.EXPIRED,
        }
    ),
}

_LEASE_TRANSITIONS: dict[LeaseState, frozenset[LeaseState]] = {
    LeaseState.ACTIVE: frozenset(
        {LeaseState.RELEASED, LeaseState.EXPIRED, LeaseState.PREEMPTED, LeaseState.INVALID}
    ),
}

_MAINTENANCE_TRANSITIONS = {
    MaintenanceTaskState.AWAITING_CONFIRMATION: frozenset({MaintenanceTaskState.CONFIRMED, MaintenanceTaskState.EXPIRED, MaintenanceTaskState.REJECTED}),
    MaintenanceTaskState.CONFIRMED: frozenset({MaintenanceTaskState.EXECUTING, MaintenanceTaskState.EXPIRED, MaintenanceTaskState.REJECTED}),
    MaintenanceTaskState.EXECUTING: frozenset({MaintenanceTaskState.COMPLETED, MaintenanceTaskState.REJECTED, MaintenanceTaskState.EXPIRED}),
}
_ROLLOUT_TRANSITIONS = {
    RolloutTaskState.PENDING: frozenset({RolloutTaskState.PREFLIGHT, RolloutTaskState.CANCELLED, RolloutTaskState.EXPIRED}),
    RolloutTaskState.PREFLIGHT: frozenset({RolloutTaskState.READY, RolloutTaskState.REJECTED, RolloutTaskState.EXPIRED}),
    RolloutTaskState.READY: frozenset({RolloutTaskState.RUNNING, RolloutTaskState.REJECTED, RolloutTaskState.EXPIRED}),
    RolloutTaskState.RUNNING: frozenset({RolloutTaskState.COMPLETED, RolloutTaskState.FAILED, RolloutTaskState.REJECTED, RolloutTaskState.EXPIRED}),
}


def transition_command(current: CommandState, target: CommandState) -> None:
    """Validate a command transition and raise on an unreachable edge."""

    if current == target:
        return
    if target not in _COMMAND_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid command transition: {current.value} -> {target.value}")


def transition_session(current: SessionState, target: SessionState) -> None:
    """Validate a session transition and raise on an unreachable edge."""

    if current == target:
        return
    if target not in _SESSION_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid session transition: {current.value} -> {target.value}")


def transition_batch(current: BatchState, target: BatchState) -> None:
    """Validate a batch aggregate transition."""

    if current == target:
        return
    if target not in _BATCH_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid batch transition: {current.value} -> {target.value}")


def transition_lease(current: LeaseState, target: LeaseState) -> None:
    """Validate a control lease transition."""

    if current == target:
        return
    if target not in _LEASE_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid lease transition: {current.value} -> {target.value}")

def transition_maintenance(current: MaintenanceTaskState, target: MaintenanceTaskState) -> None:
    if current != target and target not in _MAINTENANCE_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid maintenance transition: {current.value} -> {target.value}")

def transition_rollout(current: RolloutTaskState, target: RolloutTaskState) -> None:
    if current != target and target not in _ROLLOUT_TRANSITIONS.get(current, frozenset()):
        raise ConflictError(f"invalid rollout transition: {current.value} -> {target.value}")
