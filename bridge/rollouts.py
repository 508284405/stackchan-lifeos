"""Recoverable firmware rollout state, separate from wire transport."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from .domain import (
    AuditKind,
    AuditRecord,
    DeviceRecord,
    DeviceSession,
    RolloutTask,
    RolloutTaskState,
    transition_rollout,
    utc_now,
)
from .errors import CapabilityUnavailable, ConflictError, ValidationError
from .firmware_artifacts import FirmwareArtifact


class RolloutManager:
    def __init__(
        self,
        store,
        *,
        feature_enabled: Callable[[], bool],
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.feature_enabled = feature_enabled
        self.now = now
        self.tasks: dict[str, RolloutTask] = {}
        self._recover()

    def _audit(self, task: RolloutTask) -> None:
        self.store.append_audit(
            AuditRecord(
                kind=AuditKind.ROLLOUT_TASK_CHANGED,
                device_id=task.device_id,
                correlation_id=task.task_id,
                payload={"task": task.to_dict(), "consequence": task.consequence},
            )
        )

    def _save(self, task: RolloutTask) -> RolloutTask:
        task.updated_at = self.now()
        self.tasks[task.task_id] = task
        self.store.save_rollout_task(task)
        self._audit(task)
        return task

    def _recover(self) -> None:
        self.tasks = {task.task_id: task for task in self.store.list_rollout_tasks()}
        for task in list(self.tasks.values()):
            if task.state is RolloutTaskState.RUNNING:
                transition_rollout(task.state, RolloutTaskState.AWAITING_CONFIRMATION)
                task.state = RolloutTaskState.AWAITING_CONFIRMATION
                task.error = {
                    "code": "unconfirmed",
                    "reason": "bridge_restarted_during_update",
                }
                self._save(task)
            elif task.state in {RolloutTaskState.PREFLIGHT, RolloutTaskState.READY}:
                transition_rollout(task.state, RolloutTaskState.EXPIRED)
                task.state = RolloutTaskState.EXPIRED
                task.error = {"code": "expired", "reason": "bridge_restarted_before_update"}
                self._save(task)

    def create(self, device_id: str, image_ref: str) -> RolloutTask:
        if (
            not isinstance(image_ref, str)
            or not image_ref
            or len(image_ref) > 96
            or any(value in image_ref for value in ("/", "\\"))
        ):
            raise ValidationError("image_ref must be a bounded artifact reference")
        created = self.now()
        return self._save(
            RolloutTask(
                task_id=f"rt-{uuid4()}",
                device_id=device_id,
                image_ref=image_ref,
                created_at=created,
                updated_at=created,
            )
        )

    def preflight(
        self,
        task_id: str,
        *,
        artifact: FirmwareArtifact,
        device: DeviceRecord,
        session: DeviceSession,
        previous_sha256: str | None,
    ) -> RolloutTask:
        task = self.get(task_id)
        transition_rollout(task.state, RolloutTaskState.PREFLIGHT)
        task.state = RolloutTaskState.PREFLIGHT
        task.error = None
        self._save(task)
        manifest = artifact.manifest
        checks = {
            "signature_verified": True,
            "image_hash_verified": True,
            "hardware_compatible": manifest.hardware_id == device.hardware_id,
            "partition_compatible": "firmware_update_v1" in session.capabilities,
            "protocol_compatible": device.protocol_version == manifest.protocol_version,
            "session_online": session.device_id == device.device_id,
            "current_image_identified": previous_sha256 is not None,
        }
        task.preconditions = checks
        task.target_version = manifest.version
        task.previous_version = device.firmware_version
        task.expected_sha256 = manifest.sha256_hex
        task.previous_sha256 = previous_sha256
        task.size_bytes = manifest.size_bytes
        if not self.feature_enabled():
            task.error = {
                "code": "capability_unavailable",
                "reason": "feature_gate_disabled",
                "required_capability": "firmware_rollout",
            }
        else:
            missing = [name for name, value in checks.items() if value is not True]
            if missing:
                task.error = {
                    "code": "precondition_failed",
                    "reason": "verified_precondition_missing",
                    "missing": missing,
                }
        target = RolloutTaskState.REJECTED if task.error else RolloutTaskState.READY
        transition_rollout(task.state, target)
        task.state = target
        return self._save(task)

    def start(self, task_id: str) -> RolloutTask:
        task = self.get(task_id)
        if task.state is not RolloutTaskState.READY:
            raise CapabilityUnavailable("precondition_failed", "firmware_rollout")
        transition_rollout(task.state, RolloutTaskState.RUNNING)
        task.state = RolloutTaskState.RUNNING
        task.error = None
        task.bytes_sent = 0
        return self._save(task)

    def reject_ready(self, task_id: str, *, reason: str) -> RolloutTask:
        task = self.get(task_id)
        if task.state is not RolloutTaskState.READY:
            raise ConflictError("rollout is not ready")
        transition_rollout(task.state, RolloutTaskState.REJECTED)
        task.state = RolloutTaskState.REJECTED
        task.error = {"code": "precondition_failed", "reason": reason}
        return self._save(task)

    def note_bytes_sent(self, task_id: str, count: int) -> RolloutTask:
        task = self.get(task_id)
        if task.state is not RolloutTaskState.RUNNING or count < task.bytes_sent:
            raise ConflictError("rollout byte progress is invalid")
        task.bytes_sent = count
        return self._save(task)

    def await_confirmation(self, task_id: str) -> RolloutTask:
        task = self.get(task_id)
        if task.state is RolloutTaskState.RUNNING:
            transition_rollout(task.state, RolloutTaskState.AWAITING_CONFIRMATION)
            task.state = RolloutTaskState.AWAITING_CONFIRMATION
        elif task.state is not RolloutTaskState.AWAITING_CONFIRMATION:
            raise ConflictError("rollout is not running")
        task.error = {"code": "unconfirmed", "reason": "awaiting_device_boot_confirmation"}
        return self._save(task)

    def fail(self, task_id: str, *, reason: str) -> RolloutTask:
        task = self.get(task_id)
        if task.state not in {RolloutTaskState.RUNNING, RolloutTaskState.AWAITING_CONFIRMATION}:
            raise ConflictError("rollout cannot fail from its current state")
        transition_rollout(task.state, RolloutTaskState.FAILED)
        task.state = RolloutTaskState.FAILED
        task.error = {"code": "failed", "reason": reason}
        return self._save(task)

    def mark_session_lost(self, device_id: str, *, reason: str) -> list[RolloutTask]:
        changed: list[RolloutTask] = []
        for task in self.tasks.values():
            if task.device_id != device_id or task.state is not RolloutTaskState.RUNNING:
                continue
            transition_rollout(task.state, RolloutTaskState.AWAITING_CONFIRMATION)
            task.state = RolloutTaskState.AWAITING_CONFIRMATION
            task.error = {"code": "unconfirmed", "reason": reason}
            changed.append(self._save(task))
        return changed

    def reconcile_device(
        self,
        device_id: str,
        *,
        firmware_version: str | None,
        firmware_sha256: str | None,
        local_boot_state: str | None,
    ) -> list[RolloutTask]:
        changed: list[RolloutTask] = []
        for task in self.tasks.values():
            if task.device_id != device_id or task.state is not RolloutTaskState.AWAITING_CONFIRMATION:
                continue
            if local_boot_state != "valid":
                continue
            task.local_boot_state = local_boot_state
            if (
                firmware_version == task.target_version
                and firmware_sha256 == task.expected_sha256
                and local_boot_state == "valid"
            ):
                transition_rollout(task.state, RolloutTaskState.COMPLETED)
                task.state = RolloutTaskState.COMPLETED
                task.host_confirmed = True
                task.error = None
                task.result = {
                    "status": "completed",
                    "firmware_version": firmware_version,
                    "local_boot_state": local_boot_state,
                    "firmware_sha256": firmware_sha256,
                }
            elif (
                firmware_version == task.previous_version
                and task.previous_sha256 is not None
                and firmware_sha256 == task.previous_sha256
                and local_boot_state == "valid"
            ):
                transition_rollout(task.state, RolloutTaskState.RECOVERED)
                task.state = RolloutTaskState.RECOVERED
                task.host_confirmed = True
                task.error = None
                task.result = {
                    "status": "recovered",
                    "firmware_version": firmware_version,
                    "local_boot_state": local_boot_state,
                    "firmware_sha256": firmware_sha256,
                }
            else:
                continue
            changed.append(self._save(task))
        return changed

    def get(self, task_id: str) -> RolloutTask:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return self.tasks[task_id]

    def list(self) -> list[RolloutTask]:
        return list(self.tasks.values())
