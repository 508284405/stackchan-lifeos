"""Approval interrupt/resume with audit IDs — host-only, no device side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    RESTARTED = "restarted"


@dataclass
class ApprovalRequest:
    audit_id: str
    thread_id: str
    run_id: str
    tool_name: str
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: datetime | None = None
    resolver_note: str = ""


class ApprovalRegistry:
    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}
        self._resolved: dict[str, ApprovalRequest] = {}

    def request(self, *, thread_id: str, run_id: str, tool_name: str, reason: str) -> ApprovalRequest:
        req = ApprovalRequest(
            audit_id=str(uuid4()),
            thread_id=thread_id,
            run_id=run_id,
            tool_name=tool_name,
            reason=reason[:256],
        )
        self._pending[req.audit_id] = req
        return req

    def restore_pending(
        self,
        *,
        audit_id: str,
        thread_id: str,
        run_id: str,
        tool_name: str,
        reason: str,
    ) -> ApprovalRequest:
        """Rehydrate a pending request after the process-level registry is lost."""
        existing = self.get(audit_id)
        if existing is not None:
            return existing
        req = ApprovalRequest(
            audit_id=audit_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_name=tool_name,
            reason=reason[:256],
        )
        self._pending[audit_id] = req
        return req

    def approve(self, audit_id: str, *, note: str = "") -> ApprovalRequest | None:
        return self._resolve(audit_id, ApprovalStatus.APPROVED, note)

    def reject(self, audit_id: str, *, note: str = "") -> ApprovalRequest | None:
        return self._resolve(audit_id, ApprovalStatus.REJECTED, note)

    def timeout(self, audit_id: str) -> ApprovalRequest | None:
        return self._resolve(audit_id, ApprovalStatus.TIMEOUT, "approval timeout")

    def restart(self, audit_id: str, *, note: str = "approval restored after restart") -> ApprovalRequest | None:
        """Close an interrupted request without authorizing its side effect."""
        return self._resolve(audit_id, ApprovalStatus.RESTARTED, note)

    def get(self, audit_id: str) -> ApprovalRequest | None:
        return self._pending.get(audit_id) or self._resolved.get(audit_id)

    def pending_for_thread(self, thread_id: str) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.thread_id == thread_id and r.status is ApprovalStatus.PENDING]

    def _resolve(self, audit_id: str, status: ApprovalStatus, note: str) -> ApprovalRequest | None:
        req = self._pending.pop(audit_id, None)
        if req is None:
            return None
        req.status = status
        req.resolved_at = datetime.now(timezone.utc)
        req.resolver_note = note[:256]
        self._resolved[audit_id] = req
        return req

    def audit_log(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for req in list(self._pending.values()) + list(self._resolved.values()):
            out.append(
                {
                    "audit_id": req.audit_id,
                    "thread_id": req.thread_id,
                    "run_id": req.run_id,
                    "tool_name": req.tool_name,
                    "status": req.status.value,
                    "reason": req.reason,
                    "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
                }
            )
        return out
