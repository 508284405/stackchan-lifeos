"""Long-term store with audit, explainable writes and delete."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from copy import deepcopy
from typing import Any
import uuid


FORBIDDEN_MEMORY_KEYS = frozenset(
    {
        "raw_media",
        "image",
        "audio",
        "frame",
        "media",
        "video",
        "wav",
        "pcm",
        "jpeg",
        "png",
        "credential",
        "credentials",
        "password",
        "passwd",
        "api_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "private_key",
        "secret",
        "nonce",
        "mac",
        "serial",
        "wire_envelope",
        "device_envelope",
        "shell",
        "command",
        "url",
        "path",
    }
)


@dataclass
class MemoryRecord:
    id: str
    namespace: str
    key: str
    value: dict[str, Any]
    created_at: datetime
    reason: str
    source_run_id: str
    deleted: bool = False


@dataclass
class AuditEvent:
    id: str
    action: str
    namespace: str
    key: str
    reason: str
    run_id: str
    at: datetime


def _reject_unsafe_memory(value: Any, path: str = "value") -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"raw media or binary storage forbidden: {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            lower = str(key).lower()
            if lower in FORBIDDEN_MEMORY_KEYS or any(
                token in lower
                for token in (
                    "raw",
                    "frame",
                    "audio",
                    "image",
                    "video",
                    "credential",
                    "password",
                    "token",
                    "secret",
                    "private_key",
                    "nonce",
                    "wire",
                    "device_envelope",
                    "shell",
                    "command",
                    "url",
                    "path",
                )
            ):
                raise ValueError(f"raw media or credential storage forbidden: {path}.{key}")
            _reject_unsafe_memory(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_unsafe_memory(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"non-serializable memory value forbidden: {path}")


def _reject_raw_media(value: dict[str, Any]) -> None:
    """Backward-compatible internal name used by the store write path."""
    _reject_unsafe_memory(value)


class LifeOSStore:
    def __init__(self):
        self._data: dict[tuple[str, str], MemoryRecord] = {}
        self._audit: list[AuditEvent] = []

    def put(self, namespace: tuple[str, ...], key: str, value: dict[str, Any], *, reason: str, run_id: str) -> MemoryRecord:
        if not reason.strip():
            raise ValueError("memory write requires explainable reason")
        if not isinstance(value, dict) or not value:
            raise ValueError("memory value must be a non-empty summary object")
        if not str(key).strip() or not str(run_id).strip():
            raise ValueError("memory key and run_id are required")
        _reject_raw_media(value)
        ns = "/".join(namespace)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            namespace=ns,
            key=key,
            value=deepcopy(value),
            created_at=datetime.now(timezone.utc),
            reason=reason,
            source_run_id=run_id,
        )
        self._data[(ns, key)] = rec
        self._audit.append(AuditEvent(id=str(uuid.uuid4()), action="put", namespace=ns, key=key, reason=reason, run_id=run_id, at=rec.created_at))
        return rec

    def get(self, namespace: tuple[str, ...], key: str) -> MemoryRecord | None:
        ns = "/".join(namespace)
        rec = self._data.get((ns, key))
        if rec and rec.deleted:
            return None
        return deepcopy(rec)

    def delete(self, namespace: tuple[str, ...], key: str, *, reason: str, run_id: str) -> bool:
        if not reason.strip() or not str(run_id).strip():
            raise ValueError("memory deletion requires reason and run_id")
        ns = "/".join(namespace)
        rec = self._data.get((ns, key))
        if not rec or rec.deleted:
            return False
        rec.deleted = True
        self._audit.append(AuditEvent(id=str(uuid.uuid4()), action="delete", namespace=ns, key=key, reason=reason, run_id=run_id, at=datetime.now(timezone.utc)))
        return True

    def search(self, namespace: tuple[str, ...], query: str = "", limit: int = 8) -> list[MemoryRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("memory search limit must be a non-negative integer")
        limit = min(limit, 32)
        ns = "/".join(namespace)
        out = [r for (n, _), r in self._data.items() if n == ns and not r.deleted]
        if query:
            q = query.lower()
            out = [r for r in out if q in str(r.value).lower() or q in r.reason.lower()]
        return [deepcopy(record) for record in out[:limit]]

    def audit_log(self) -> list[AuditEvent]:
        return deepcopy(self._audit)

    def recall(self, namespace: tuple[str, ...], query: str = "", *, limit: int = 4) -> list[dict[str, Any]]:
        """Return explainable memory snippets for graph context."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("memory recall limit must be a non-negative integer")
        limit = min(limit, 16)
        records = self.search(namespace, query=query, limit=limit)
        return [
            {
                "id": rec.id,
                "key": rec.key,
                "summary": rec.value.get("summary") or rec.value.get("text") or str(rec.value)[:120],
                "reason": rec.reason,
                "created_at": rec.created_at.isoformat(),
            }
            for rec in records
        ]
