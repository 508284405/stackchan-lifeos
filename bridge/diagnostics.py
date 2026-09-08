"""Deterministic, safe diagnostic export."""
from __future__ import annotations
import hashlib, json
from datetime import datetime
from typing import Any
from .domain import AuditKind, AuditRecord, utc_now

DEVICE_FIELDS = frozenset({"device_id", "hardware_id", "display_name", "site_id", "firmware_version", "protocol_version", "capabilities", "lifecycle_state"})
HEALTH_FIELDS = frozenset({"heap", "uptime_ms", "faults"})
SENSITIVE = frozenset({"nonce", "key", "secret", "token", "password", "path", "media", "payload_raw", "raw_media"})

def _safe(value: Any, *, allowed: frozenset[str] | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items() if (allowed is None or k in allowed) and k not in SENSITIVE}
    if isinstance(value, list): return [_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None: return value
    return str(value)

def export_diagnostics(device, session, health: dict[str, Any] | None, audits: list[AuditRecord], *, now: datetime = utc_now) -> dict[str, Any]:
    content = {"device": _safe(device.to_dict(), allowed=DEVICE_FIELDS),
               "session": {"session_id": session.session_id, "device_id": session.device_id, "state": session.state.value, "capabilities": sorted(session.capabilities)} if session else None,
               "health": _safe(health or {}, allowed=HEALTH_FIELDS),
               "audit": [{"audit_id": a.audit_id, "kind": a.kind.value, "occurred_at": a.occurred_at.isoformat()} for a in audits]}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result = {"bundle_id": "diag-" + hashlib.sha256(canonical).hexdigest()[:24], "generated_at": now().isoformat(), "content": content}
    result["integrity_sha256"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
