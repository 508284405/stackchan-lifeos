"""Semantic lifeos.v1 command projection — no PWM, angles, or raw hardware."""

from __future__ import annotations

from copy import deepcopy
import json
import re
import time
from typing import Any, Callable, Iterable

from .models import BehaviorIntent, LifeEvent, LifeState

SCHEMA_VERSION = "lifeos.v1"


class LocalToolExecutor:
    """Explicit local-tool allowlist with argument validation and idempotency.

    The default registry is empty. A caller must register a narrowly scoped
    local handler; remote provider output can never create a handler or invoke
    shell/network/device primitives through this class.
    """

    _SAFE_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
    _FORBIDDEN_TOOL_NAMES = frozenset({"shell", "exec", "execute", "network", "filesystem", "device", "device.write", "file.write"})
    _FORBIDDEN_ARG_PARTS = ("raw", "path", "url", "shell", "command", "token", "secret", "credential", "device")

    def __init__(self, allowed_tools: Iterable[str] = ()):
        self._allowed = set(allowed_tools)
        self._handlers: dict[str, tuple[Callable[[dict[str, Any]], Any], frozenset[str]]] = {}
        self._completed: dict[str, Any] = {}

    def register(self, name: str, handler: Callable[[dict[str, Any]], Any], *, allowed_args: Iterable[str] = ()) -> None:
        if not self._SAFE_NAME.fullmatch(name) or name not in self._allowed or name in self._FORBIDDEN_TOOL_NAMES:
            raise ValueError("tool is not in the local allowlist")
        args = frozenset(allowed_args)
        if any(not isinstance(key, str) or not self._SAFE_NAME.fullmatch(key) for key in args):
            raise ValueError("tool argument names must be safe identifiers")
        self._handlers[name] = (handler, args)

    @classmethod
    def _validate_args(cls, args: dict[str, Any], allowed_args: frozenset[str]) -> None:
        if not isinstance(args, dict) or set(args) - allowed_args:
            raise ValueError("tool arguments are outside the registered schema")
        def check_keys(value: Any) -> None:
            if isinstance(value, dict):
                lowered = {str(key).lower() for key in value}
                if any(any(part in key for part in cls._FORBIDDEN_ARG_PARTS) for key in lowered):
                    raise ValueError("tool arguments contain a forbidden capability")
                for child in value.values():
                    check_keys(child)
            elif isinstance(value, list):
                for child in value:
                    check_keys(child)

        check_keys(args)
        try:
            encoded = json.dumps(args, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool arguments must be bounded JSON") from exc
        if len(encoded) > 2048:
            raise ValueError("tool arguments exceed size budget")

    def execute(self, tool_intent: Any, *, idempotency_key: str) -> dict[str, Any]:
        name = getattr(tool_intent, "name", None)
        args = getattr(tool_intent, "args", None)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("tool execution requires an idempotency key")
        if idempotency_key in self._completed:
            return {"executed": False, "duplicate": True, "result": deepcopy(self._completed[idempotency_key])}
        if name not in self._handlers:
            raise ValueError("tool intent is not registered")
        handler, allowed_args = self._handlers[name]
        self._validate_args(args, allowed_args)
        result = handler(dict(args))
        try:
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("tool result must be bounded JSON") from exc
        self._completed[idempotency_key] = deepcopy(result)
        return {"executed": True, "duplicate": False, "result": deepcopy(result)}


def project_command(
    intent: BehaviorIntent,
    event: LifeEvent,
    state: LifeState,
    *,
    idempotency_key: str | None = None,
    seq: int = 0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Project a validated semantic intent into the canonical ``lifeos.v1`` envelope."""
    key = idempotency_key or f"{event.id}:{intent.name}"
    if not isinstance(key, str) or not key or len(key) > 96:
        raise ValueError("command idempotency key must be a bounded string")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError("command sequence must be a non-negative integer")
    if now_ms is not None and (isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0):
        raise ValueError("command timestamp must be a non-negative integer")
    issued_at_ms = int(time.time() * 1000) if now_ms is None else now_ms
    ttl_ms = max(1, min(int(float(intent.ttl_seconds) * 1000), 1500))
    payload: dict[str, Any] = {
        "behavior": intent.name,
        "intensity": round(float(intent.intensity), 3),
        "interruptible": bool(intent.interruptible),
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": issued_at_ms + ttl_ms,
    }
    if intent.speech:
        payload["speech"] = intent.speech[:512]
    return {
        "schema": SCHEMA_VERSION,
        "kind": "command",
        "type": "command.intent",
        "event_id": key,
        "command_id": key,
        "correlation_id": event.id,
        "device_id": state.device_id,
        "seq": seq,
        "ts_ms": issued_at_ms,
        "payload": payload,
    }


def validate_projection(envelope: dict[str, Any]) -> None:
    required = {"schema", "kind", "type", "event_id", "device_id", "seq", "ts_ms", "payload"}
    allowed = required | {"command_id", "correlation_id"}
    if set(envelope) - allowed:
        raise ValueError(f"projection contains non-contract fields: {sorted(set(envelope) - allowed)}")
    if envelope.get("schema") != SCHEMA_VERSION:
        raise ValueError("projection must use lifeos.v1")
    if envelope.get("kind") != "command" or envelope.get("type") != "command.intent":
        raise ValueError("projection command is not semantic")
    missing = required - set(envelope)
    if missing:
        raise ValueError(f"projection is missing required fields: {sorted(missing)}")
    for field, maximum in (("event_id", 96), ("device_id", 64)):
        if not isinstance(envelope.get(field), str) or not envelope[field] or len(envelope[field]) > maximum:
            raise ValueError(f"projection requires a bounded {field}")
    if "command_id" in envelope and (not isinstance(envelope["command_id"], str) or not envelope["command_id"] or len(envelope["command_id"]) > 96):
        raise ValueError("projection command_id must be a non-empty string")
    if "correlation_id" in envelope and (not isinstance(envelope["correlation_id"], str) or not envelope["correlation_id"] or len(envelope["correlation_id"]) > 96):
        raise ValueError("projection correlation_id must be a non-empty string")
    if isinstance(envelope.get("seq"), bool) or not isinstance(envelope.get("seq"), int) or envelope["seq"] < 0:
        raise ValueError("projection seq must be a non-negative integer")
    if isinstance(envelope.get("ts_ms"), bool) or not isinstance(envelope.get("ts_ms"), int) or envelope["ts_ms"] < 0:
        raise ValueError("projection ts_ms must be a non-negative integer")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("behavior"), str) or not payload["behavior"] or len(payload["behavior"]) > 64:
        raise ValueError("projection payload must contain a behavior")
    payload_allowed = {"behavior", "intensity", "interruptible", "speech", "issued_at_ms", "expires_at_ms"}
    extra = set(payload) - payload_allowed
    if extra:
        raise ValueError(f"projection contains non-semantic fields: {sorted(extra)}")
    intensity = payload.get("intensity")
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not 0.0 <= float(intensity) <= 1.0:
        raise ValueError("projection intensity is outside the semantic range")
    if not isinstance(payload.get("interruptible"), bool):
        raise ValueError("projection interruptible must be boolean")
    if any(isinstance(payload.get(field), bool) or not isinstance(payload.get(field), int) or payload[field] < 0 for field in ("issued_at_ms", "expires_at_ms")):
        raise ValueError("projection requires integer TTL fields")
    if payload["expires_at_ms"] <= payload["issued_at_ms"]:
        raise ValueError("projection TTL must be positive")
    if payload["issued_at_ms"] != envelope["ts_ms"]:
        raise ValueError("projection timestamp and issued_at_ms must match")
    if payload["expires_at_ms"] - payload["issued_at_ms"] > 1500:
        raise ValueError("projection TTL exceeds device command bound")
    if "speech" in payload and (not isinstance(payload["speech"], str) or len(payload["speech"]) > 512):
        raise ValueError("projection speech is outside the semantic range")
    try:
        json.dumps(envelope, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("projection must be bounded JSON") from exc
