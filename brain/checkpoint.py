"""Small local checkpoint stores with versioned recovery and idempotent outbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import json
from pathlib import Path
import os
import tempfile
from typing import Any

GRAPH_VERSION = "lifeos-graph.v1"
PROVIDER_CONTRACT_VERSION = "sub2api.v1"
_FORBIDDEN_CHECKPOINT_KEYS = frozenset(
    {
        "raw_media",
        "image",
        "audio",
        "frame",
        "video",
        "api_key",
        "token",
        "secret",
        "password",
        "nonce",
        "mac",
        "serial",
        "wire_envelope",
        "device_envelope",
        "tool_output",
    }
)


def _validate_safe_snapshot(value: Any, path: str = "checkpoint") -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"checkpoint cannot store binary data: {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            lower = str(key).lower()
            if lower in _FORBIDDEN_CHECKPOINT_KEYS or any(part in lower for part in ("api_key", "raw_media", "wire_envelope")):
                raise ValueError(f"checkpoint contains forbidden field: {path}.{key}")
            _validate_safe_snapshot(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_safe_snapshot(child, f"{path}[{index}]")


@dataclass
class Checkpoint:
    thread_id: str
    graph_version: str = GRAPH_VERSION
    provider_contract_version: str = PROVIDER_CONTRACT_VERSION
    state: dict[str, Any] = field(default_factory=dict)
    outbox: list[dict[str, Any]] = field(default_factory=list)
    pending_intent: dict[str, Any] | None = None
    approval_audit_id: str | None = None
    approval_status: str | None = None


class MemoryCheckpointer:
    def __init__(self):
        self._store: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        # Checkpoints are snapshots.  Callers must not be able to mutate the
        # recovery record through a state or outbox object they still hold.
        _validate_safe_snapshot(checkpoint.state, "state")
        _validate_safe_snapshot(checkpoint.pending_intent, "pending_intent")
        _validate_safe_snapshot(checkpoint.outbox, "outbox")
        self._store[checkpoint.thread_id] = deepcopy(checkpoint)
        self._after_mutation()

    def load(self, thread_id: str) -> Checkpoint | None:
        checkpoint = self._store.get(thread_id)
        return deepcopy(checkpoint) if checkpoint is not None else None

    def restore(self, thread_id: str, *, graph_version: str = GRAPH_VERSION, provider_contract_version: str = PROVIDER_CONTRACT_VERSION) -> Checkpoint | None:
        cp = self._store.get(thread_id)
        if cp is None:
            return None
        if cp.graph_version != graph_version or cp.provider_contract_version != provider_contract_version:
            return None
        return deepcopy(cp)

    def append_outbox(self, thread_id: str, entry: dict[str, Any]) -> bool:
        _validate_safe_snapshot(entry, "outbox")
        cp = self._store.get(thread_id)
        if cp is None:
            cp = Checkpoint(thread_id=thread_id)
            self._store[thread_id] = cp
        key = entry.get("idempotency_key")
        if not isinstance(key, str) or not key:
            raise ValueError("outbox entries require an idempotency_key")
        if any(e.get("idempotency_key") == key for e in cp.outbox):
            return False
        cp.outbox.append(deepcopy(entry))
        self._after_mutation()
        return True

    def clear_outbox(self, thread_id: str, idempotency_key: str) -> None:
        cp = self._store.get(thread_id)
        if cp is None:
            return
        cp.outbox = [e for e in cp.outbox if e.get("idempotency_key") != idempotency_key]
        self._after_mutation()

    def _after_mutation(self) -> None:
        """Hook for durable implementations; memory storage has no side effect."""


class JsonFileCheckpointer(MemoryCheckpointer):
    """Durable, single-host checkpoint store without a database dependency.

    The file contains only JSON-safe graph snapshots and outbox entries. It is
    intentionally a local development/single-process store; deployments that
    need concurrent writers should replace it with a transactional backend.
    """

    def __init__(self, path: str | os.PathLike[str]):
        super().__init__()
        self.path = Path(path)
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("checkpoint file is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError("checkpoint file must contain an object")
        for thread_id, item in payload.items():
            if not isinstance(thread_id, str) or not isinstance(item, dict):
                raise ValueError("checkpoint file contains an invalid entry")
            self._store[thread_id] = self._from_json(item)

    @staticmethod
    def _from_json(item: dict[str, Any]) -> Checkpoint:
        allowed = {
            "thread_id",
            "graph_version",
            "provider_contract_version",
            "state",
            "outbox",
            "pending_intent",
            "approval_audit_id",
            "approval_status",
        }
        if set(item) - allowed:
            raise ValueError("checkpoint contains unknown fields")
        thread_id = item.get("thread_id")
        state = item.get("state", {})
        outbox = item.get("outbox", [])
        if not isinstance(thread_id, str) or not isinstance(state, dict) or not isinstance(outbox, list):
            raise ValueError("checkpoint fields have invalid types")
        pending_intent = item.get("pending_intent")
        _validate_safe_snapshot(state, "state")
        _validate_safe_snapshot(outbox, "outbox")
        _validate_safe_snapshot(pending_intent, "pending_intent")
        return Checkpoint(
            thread_id=thread_id,
            graph_version=item.get("graph_version", GRAPH_VERSION),
            provider_contract_version=item.get("provider_contract_version", PROVIDER_CONTRACT_VERSION),
            state=state,
            outbox=outbox,
            pending_intent=pending_intent,
            approval_audit_id=item.get("approval_audit_id"),
            approval_status=item.get("approval_status"),
        )

    def _after_mutation(self) -> None:
        payload = {
            thread_id: {
                "thread_id": cp.thread_id,
                "graph_version": cp.graph_version,
                "provider_contract_version": cp.provider_contract_version,
                "state": cp.state,
                "outbox": cp.outbox,
                "pending_intent": cp.pending_intent,
                "approval_audit_id": cp.approval_audit_id,
                "approval_status": cp.approval_status,
            }
            for thread_id, cp in self._store.items()
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
