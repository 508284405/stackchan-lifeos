"""Host-only single-target firmware rollout task framework."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from .domain import AuditKind, AuditRecord, RolloutTask, RolloutTaskState, transition_rollout, utc_now
from .errors import CapabilityUnavailable, ValidationError

REQUIRED = ("signature_verified", "hardware_compatible", "partition_compatible", "protocol_compatible")

class RolloutManager:
    def __init__(self, store, *, feature_enabled, now=utc_now):
        self.store, self.feature_enabled, self.now, self.tasks = store, feature_enabled, now, {}
        self._recover()
    def _audit(self, task):
        self.store.append_audit(
            AuditRecord(
                kind=AuditKind.ROLLOUT_TASK_CHANGED,
                device_id=task.device_id,
                correlation_id=task.task_id,
                payload={"task": task.to_dict(), "consequence": task.consequence},
            )
        )
    def _save(self, task):
        task.updated_at = self.now()
        self.tasks[task.task_id] = task
        self.store.save_rollout_task(task)
        self._audit(task)
    def _recover(self):
        self.tasks = {task.task_id: task for task in self.store.list_rollout_tasks()}
        for task in self.tasks.values():
            if task.state in {RolloutTaskState.PREFLIGHT, RolloutTaskState.READY, RolloutTaskState.RUNNING}:
                transition_rollout(task.state, RolloutTaskState.EXPIRED); task.state, task.error = RolloutTaskState.EXPIRED, {"code": "expired", "reason": "bridge_restarted"}; self._save(task)
    def create(self, device_id: str, image_ref: str) -> RolloutTask:
        if not isinstance(image_ref, str) or not image_ref or len(image_ref) > 256 or any(x in image_ref for x in ("/", "\\")): raise ValidationError("image_ref must be a bounded artifact reference")
        task = RolloutTask(f"rt-{uuid4()}", device_id, image_ref, created_at=self.now(), updated_at=self.now())
        self._save(task); return task
    def preflight(self, task_id: str, *, checks: dict[str, bool] | None = None) -> RolloutTask:
        task = self.tasks[task_id]; transition_rollout(task.state, RolloutTaskState.PREFLIGHT); task.state = RolloutTaskState.PREFLIGHT; self._save(task)
        checks = checks or {}
        missing = [name for name in REQUIRED if checks.get(name) is not True]
        task.preconditions = {name: bool(checks.get(name, False)) for name in REQUIRED}
        if not self.feature_enabled(): task.error = {"code": "capability_unavailable", "reason": "feature_gate_disabled", "required_capability": "firmware_rollout"}
        elif missing: task.error = {"code": "precondition_failed", "reason": "required_precondition_missing", "missing": missing}
        else: task.state = RolloutTaskState.READY
        if task.error: task.state = RolloutTaskState.REJECTED
        self._save(task); return task
    def execute(self, task_id: str) -> RolloutTask:
        task = self.tasks[task_id]
        if task.state is not RolloutTaskState.READY: raise CapabilityUnavailable("precondition_failed", "firmware_rollout")
        task.state, task.error = RolloutTaskState.REJECTED, {"code": "capability_unavailable", "reason": "ota_wire_not_implemented"}; self._save(task); return task
    def get(self, task_id):
        if task_id not in self.tasks: raise KeyError(task_id)
        return self.tasks[task_id]
