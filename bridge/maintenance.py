"""Host-only maintenance challenge and task boundary."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .domain import (
    AuditKind,
    AuditRecord,
    MaintenanceTask,
    MaintenanceTaskState,
    SessionState,
    transition_maintenance,
    utc_now,
)
from .errors import CapabilityUnavailable, ConflictError, ProtocolError, ValidationError

MAX_OPERATION_LENGTH = 64
CONFIRMATION_CAPABILITY = "maintenance_confirmation"
EXECUTE_CAPABILITY = "maintenance_execute"


class MaintenanceManager:
    def __init__(self, store, *, feature_enabled: Callable[[], bool], now: Callable[[], datetime] = utc_now,
                 session_lookup: Callable[[str], Any] | None = None):
        self.store, self.feature_enabled, self.now = store, feature_enabled, now
        self.session_lookup = session_lookup
        self.challenges: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, MaintenanceTask] = {}
        self._recover()

    def _audit(self, kind, payload, *, task: MaintenanceTask | None = None):
        self.store.append_audit(
            AuditRecord(
                kind=kind,
                device_id=task.device_id if task is not None else None,
                session_id=task.session_id if task is not None else None,
                correlation_id=task.task_id if task is not None else None,
                payload=payload,
            )
        )

    def _recover(self):
        self.tasks = {task.task_id: task for task in self.store.list_maintenance_tasks()}
        for task in self.tasks.values():
            if task.state in {MaintenanceTaskState.EXECUTING, MaintenanceTaskState.CONFIRMED,
                              MaintenanceTaskState.AWAITING_CONFIRMATION}:
                transition_maintenance(task.state, MaintenanceTaskState.EXPIRED)
                task.state = MaintenanceTaskState.EXPIRED
                task.error = {"code": "expired", "reason": "bridge_restarted"}
                self._save(task)

    def _save(self, task: MaintenanceTask):
        task.updated_at = self.now()
        self.tasks[task.task_id] = task
        self.store.save_maintenance_task(task)
        self._audit(
            AuditKind.MAINTENANCE_TASK_CHANGED,
            {"task": task.to_dict(), "consequence": task.consequence},
            task=task,
        )

    def prepare(self, device_id: str, session_id: str, operation: str, *, ttl_ms: int = 60_000,
                capabilities: set[str] | frozenset[str] = frozenset()) -> MaintenanceTask:
        if not isinstance(operation, str) or not operation or len(operation) > MAX_OPERATION_LENGTH:
            raise ValidationError("operation must be a bounded non-empty string")
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 1 <= ttl_ms <= 300_000:
            raise ValidationError("ttl_ms must be between 1 and 300000")
        if not self.feature_enabled():
            raise CapabilityUnavailable("feature_gate_disabled", "maintenance")
        if CONFIRMATION_CAPABILITY not in capabilities:
            raise CapabilityUnavailable("device_not_declared", CONFIRMATION_CAPABILITY)
        challenge_id = f"mch-{uuid4()}"
        created_at = self.now()
        task = MaintenanceTask(
            f"mt-{uuid4()}",
            device_id,
            operation,
            session_id,
            challenge_id,
            created_at=created_at,
            updated_at=created_at,
        )
        self.challenges[challenge_id] = {
            "task_id": task.task_id,
            "device_id": device_id,
            "session_id": session_id,
            "operation": operation,
            "expires_at": created_at + timedelta(milliseconds=ttl_ms),
            "used": False,
        }
        self._save(task)
        self._audit(
            AuditKind.MAINTENANCE_CHALLENGE_CHANGED,
            {"challenge_id": challenge_id, "task_id": task.task_id, "state": "prepared"},
            task=task,
        )
        return task

    def confirm(
        self,
        challenge_id: str,
        *,
        device_id: str,
        session_id: str,
        operation: str,
        result: str,
        valid_for_ms: int,
    ) -> MaintenanceTask:
        challenge = self.challenges.get(challenge_id)
        if challenge is None or challenge["device_id"] != device_id or challenge["session_id"] != session_id:
            raise ConflictError("maintenance challenge is invalid or session-bound")
        if challenge["operation"] != operation:
            raise ConflictError("maintenance confirmation operation does not match challenge")
        if result != "confirmed":
            raise ConflictError("maintenance confirmation result is not accepted")
        if not isinstance(valid_for_ms, int) or isinstance(valid_for_ms, bool) or not 1 <= valid_for_ms <= 300_000:
            raise ValidationError("maintenance confirmation valid_for_ms must be between 1 and 300000")
        now = self.now()
        if challenge["used"] or now >= challenge["expires_at"]:
            raise ConflictError("maintenance challenge is expired or already used")
        if now + timedelta(milliseconds=valid_for_ms) > challenge["expires_at"]:
            raise ConflictError("maintenance confirmation exceeds challenge expiry")
        if self.session_lookup is not None:
            try:
                current_session = self.session_lookup(session_id)
            except ProtocolError as exc:
                raise ConflictError("maintenance confirmation session is no longer available") from exc
            if current_session.state not in {
                SessionState.ONLINE,
                SessionState.DEGRADED,
                SessionState.MAINTENANCE,
            }:
                raise ConflictError("maintenance confirmation session is not active")
        challenge["used"] = True
        task = self.tasks[challenge["task_id"]]
        if task.state is not MaintenanceTaskState.AWAITING_CONFIRMATION:
            raise ConflictError("maintenance task is not awaiting confirmation")
        transition_maintenance(task.state, MaintenanceTaskState.CONFIRMED)
        task.state = MaintenanceTaskState.CONFIRMED
        self._save(task)
        self._audit(
            AuditKind.MAINTENANCE_CHALLENGE_CHANGED,
            {"challenge_id": challenge_id, "task_id": task.task_id, "state": "confirmed"},
            task=task,
        )
        return task

    def expire(self, task_id: str, *, reason: str) -> MaintenanceTask:
        task = self.get(task_id)
        if task.state not in {
            MaintenanceTaskState.AWAITING_CONFIRMATION,
            MaintenanceTaskState.CONFIRMED,
            MaintenanceTaskState.EXECUTING,
        }:
            return task
        transition_maintenance(task.state, MaintenanceTaskState.EXPIRED)
        task.state = MaintenanceTaskState.EXPIRED
        task.error = {"code": "expired", "reason": reason}
        self._save(task)
        return task

    def expire_for_session(self, session_id: str, *, reason: str) -> list[MaintenanceTask]:
        return [
            self.expire(task.task_id, reason=reason)
            for task in list(self.tasks.values())
            if task.session_id == session_id
            and task.state
            in {
                MaintenanceTaskState.AWAITING_CONFIRMATION,
                MaintenanceTaskState.CONFIRMED,
                MaintenanceTaskState.EXECUTING,
            }
        ]

    def execute(self, task_id: str, *, capabilities: set[str] | frozenset[str]) -> MaintenanceTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise ConflictError("maintenance task not found")
        if task.state is not MaintenanceTaskState.CONFIRMED:
            raise ConflictError("maintenance task is not confirmed")
        if not self.feature_enabled():
            transition_maintenance(task.state, MaintenanceTaskState.REJECTED); task.state, task.error = MaintenanceTaskState.REJECTED, {"code": "capability_unavailable", "reason": "feature_gate_disabled"}
        elif EXECUTE_CAPABILITY not in capabilities:
            transition_maintenance(task.state, MaintenanceTaskState.REJECTED); task.state, task.error = MaintenanceTaskState.REJECTED, {"code": "capability_unavailable", "reason": "device_not_declared", "required_capability": EXECUTE_CAPABILITY}
        else:
            # The capability is intentionally not wired in this release.
            transition_maintenance(task.state, MaintenanceTaskState.REJECTED); task.state, task.error = MaintenanceTaskState.REJECTED, {"code": "capability_unavailable", "reason": "protocol_not_implemented", "required_capability": EXECUTE_CAPABILITY}
        self._save(task)
        return task

    def get(self, task_id: str) -> MaintenanceTask:
        if task_id not in self.tasks:
            raise ConflictError("maintenance task not found")
        return self.tasks[task_id]
