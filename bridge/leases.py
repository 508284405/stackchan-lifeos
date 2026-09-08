"""Host-side control lease and dead-man validation for manual_control_v1."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .domain import ControlLease, DeviceSession, LeaseState, SessionState, transition_lease, utc_now
from .errors import ConflictError, NotFoundError, ValidationError


MIN_INPUT_TTL_MS = 300
MAX_INPUT_TTL_MS = 500
DEFAULT_INPUT_TTL_MS = 400
DEFAULT_MAX_LEASE_MS = 30_000


class ControlLeaseManager:
    """Keep leases ephemeral and bound to a single session/connection."""

    def __init__(self, *, now_factory: Callable[[], datetime] = utc_now) -> None:
        self._now = now_factory
        self._leases: dict[str, ControlLease] = {}
        self._active_by_device: dict[str, str] = {}

    @staticmethod
    def _check_connection(connection_id: str) -> None:
        if not isinstance(connection_id, str) or not connection_id or len(connection_id) > 96:
            raise ValidationError("connection_id must be a bounded non-empty string")

    @staticmethod
    def _check_ttl(ttl_ms: int) -> None:
        if (
            not isinstance(ttl_ms, int)
            or isinstance(ttl_ms, bool)
            or not MIN_INPUT_TTL_MS <= ttl_ms <= MAX_INPUT_TTL_MS
        ):
            raise ValidationError(f"manual input ttl_ms must be between {MIN_INPUT_TTL_MS} and {MAX_INPUT_TTL_MS}")

    def acquire(
        self,
        session: DeviceSession,
        connection_id: str,
        *,
        ttl_ms: int = DEFAULT_INPUT_TTL_MS,
        max_duration_ms: int = DEFAULT_MAX_LEASE_MS,
        now: datetime | None = None,
    ) -> ControlLease:
        if session.state is not SessionState.ONLINE:
            raise ConflictError("control lease requires an online session")
        if "manual_control_v1" not in session.capabilities:
            raise ConflictError("device did not declare manual_control_v1")
        self._check_connection(connection_id)
        self._check_ttl(ttl_ms)
        if (
            not isinstance(max_duration_ms, int)
            or isinstance(max_duration_ms, bool)
            or max_duration_ms < ttl_ms
            or max_duration_ms > 120_000
        ):
            raise ValidationError("max_duration_ms must be >= ttl_ms and <= 120000")
        current = now or self._now()
        self.expire(now=current)
        active_id = self._active_by_device.get(session.device_id)
        if active_id:
            raise ConflictError("device already has an active control lease")
        max_expires_at = current + timedelta(milliseconds=max_duration_ms)
        lease = ControlLease(
            lease_id=f"lease-{uuid4()}",
            device_id=session.device_id,
            session_id=session.session_id,
            connection_id=connection_id,
            issued_at=current,
            expires_at=min(current + timedelta(milliseconds=ttl_ms), max_expires_at),
            max_expires_at=max_expires_at,
        )
        self._leases[lease.lease_id] = lease
        self._active_by_device[session.device_id] = lease.lease_id
        return lease

    def get(self, lease_id: str) -> ControlLease:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise NotFoundError(f"control lease not found: {lease_id}")
        return lease

    def _assert_binding(
        self,
        lease: ControlLease,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        connection_id: str | None = None,
    ) -> None:
        if device_id is not None and lease.device_id != device_id:
            raise ConflictError("control lease device mismatch")
        if session_id is not None and lease.session_id != session_id:
            raise ConflictError("control lease session mismatch")
        if connection_id is not None and lease.connection_id != connection_id:
            raise ConflictError("control lease connection mismatch")

    def _expire_one(self, lease: ControlLease, current: datetime) -> bool:
        if lease.state is not LeaseState.ACTIVE:
            return False
        if current < lease.expires_at and current < lease.max_expires_at:
            return False
        transition_lease(lease.state, LeaseState.EXPIRED)
        lease.state = LeaseState.EXPIRED
        lease.reason = "ttl_elapsed"
        if self._active_by_device.get(lease.device_id) == lease.lease_id:
            del self._active_by_device[lease.device_id]
        return True

    def expire(self, *, now: datetime | None = None) -> list[ControlLease]:
        current = now or self._now()
        expired: list[ControlLease] = []
        for lease in self._leases.values():
            if self._expire_one(lease, current):
                expired.append(lease)
        return expired

    def renew(
        self,
        lease_id: str,
        connection_id: str,
        *,
        ttl_ms: int = DEFAULT_INPUT_TTL_MS,
        now: datetime | None = None,
    ) -> ControlLease:
        lease = self.get(lease_id)
        self._assert_binding(lease, connection_id=connection_id)
        self._check_ttl(ttl_ms)
        current = now or self._now()
        if self._expire_one(lease, current):
            raise ConflictError("control lease has expired")
        if lease.state is not LeaseState.ACTIVE:
            raise ConflictError(f"control lease is {lease.state.value}")
        lease.expires_at = min(current + timedelta(milliseconds=ttl_ms), lease.max_expires_at)
        if lease.expires_at <= current:
            self._expire_one(lease, current)
            raise ConflictError("control lease reached its maximum lifetime")
        return lease

    def release(self, lease_id: str, connection_id: str, *, reason: str = "released") -> ControlLease:
        lease = self.get(lease_id)
        self._assert_binding(lease, connection_id=connection_id)
        if lease.state is LeaseState.ACTIVE:
            transition_lease(lease.state, LeaseState.RELEASED)
            lease.state = LeaseState.RELEASED
            lease.reason = reason
            if self._active_by_device.get(lease.device_id) == lease.lease_id:
                del self._active_by_device[lease.device_id]
        return lease

    def preempt(self, lease_id: str, *, reason: str) -> ControlLease:
        lease = self.get(lease_id)
        if lease.state is LeaseState.ACTIVE:
            transition_lease(lease.state, LeaseState.PREEMPTED)
            lease.state = LeaseState.PREEMPTED
            lease.reason = reason
            if self._active_by_device.get(lease.device_id) == lease.lease_id:
                del self._active_by_device[lease.device_id]
        return lease

    def invalidate_session(self, session_id: str, *, reason: str) -> list[ControlLease]:
        changed: list[ControlLease] = []
        for lease in self._leases.values():
            if lease.session_id == session_id and lease.state is LeaseState.ACTIVE:
                transition_lease(lease.state, LeaseState.INVALID)
                lease.state = LeaseState.INVALID
                lease.reason = reason
                if self._active_by_device.get(lease.device_id) == lease.lease_id:
                    del self._active_by_device[lease.device_id]
                changed.append(lease)
        return changed

    def accept_input(
        self,
        lease_id: str,
        *,
        device_id: str,
        session_id: str,
        connection_id: str,
        input_seq: int,
        action: str,
        direction: dict[str, Any] | None,
        ttl_ms: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        lease = self.get(lease_id)
        self._assert_binding(
            lease,
            device_id=device_id,
            session_id=session_id,
            connection_id=connection_id,
        )
        current = now or self._now()
        if self._expire_one(lease, current):
            raise ConflictError("control lease has expired")
        if lease.state is not LeaseState.ACTIVE:
            raise ConflictError(f"control lease is {lease.state.value}")
        self._check_ttl(ttl_ms)
        if action not in {"input", "release"}:
            raise ValidationError("manual control action must be input or release")
        if not isinstance(input_seq, int) or isinstance(input_seq, bool) or input_seq < 1:
            raise ValidationError("input_seq must be a positive integer")
        if input_seq != lease.last_input_seq + 1:
            raise ConflictError("manual control input sequence is not consecutive")
        if action == "input":
            if lease.last_input_at is not None:
                input_gap_ms = (current - lease.last_input_at).total_seconds() * 1000
                if input_gap_ms < 100:
                    raise ConflictError("manual control input rate exceeds 10 Hz")
            if not isinstance(direction, dict) or set(direction) != {"yaw", "pitch"}:
                raise ValidationError("input direction must contain only yaw and pitch")
            for axis in ("yaw", "pitch"):
                value = direction[axis]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValidationError("manual direction values must be finite numbers")
                if not -1 <= value <= 1:
                    raise ValidationError("manual direction values must be in [-1, 1]")
            normalized_direction = {
                "yaw": float(direction["yaw"]),
                "pitch": float(direction["pitch"]),
            }
        else:
            if direction is not None:
                raise ValidationError("release input cannot contain direction")
            normalized_direction = None
        lease.last_input_seq = input_seq
        if action == "input":
            lease.last_input_at = current
            # Every accepted dead-man input renews the short lease window.
            # Without this sliding expiry, the browser's 120 ms keepalive
            # cadence outlives the initial 400 ms lease and the next frame is
            # rejected even though the operator is still holding the key.
            lease.expires_at = min(current + timedelta(milliseconds=ttl_ms), lease.max_expires_at)
        if action == "release":
            self.release(lease_id, connection_id, reason="input_release")
        return {
            "lease_id": lease_id,
            "input_seq": input_seq,
            "action": action,
            **({"direction": normalized_direction} if normalized_direction is not None else {}),
            "ttl_ms": ttl_ms,
        }

    def list(self) -> list[ControlLease]:
        return list(self._leases.values())
