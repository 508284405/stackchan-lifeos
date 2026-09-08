"""Provider-boundary data redaction — field allowlist only."""

from __future__ import annotations

import re
from typing import Any

from .models import LifeEvent, LifeState, MAX_PROVIDER_CONTEXT_CHARS, MAX_PROVIDER_REDACTED_ITEMS


FORBIDDEN_KEYS = frozenset(
    {
        "raw_media",
        "image",
        "audio",
        "frame",
        "media",
        "path",
        "filepath",
        "cwd",
        "env",
        "environment",
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "nonce",
        "hardware_id",
        "mac",
        "serial",
        "wire_envelope",
        "device_envelope",
        "lifeos_envelope",
        "tool_output",
        "reasoning",
        "chain_of_thought",
    }
)

FORBIDDEN_SUBSTRINGS = ("raw", "secret", "credential", "private_key")

MAX_TEXT_CHARS = 512
MAX_RECENT_TEXTS = 8
ALLOWED_EVENT_KINDS = frozenset({"user", "touch", "presence", "vision", "motion", "timer", "system"})
ALLOWED_MOODS = frozenset({"calm", "happy", "curious", "sleepy", "alert", "sad"})


def _contains_forbidden_key(obj: Any, depth: int = 0) -> bool:
    # Structured-output schemas legitimately nest several levels; keep a finite
    # guard without rejecting the provider's bounded JSON schema itself.
    if depth > 16:
        return True
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = str(key).lower()
            if lower in FORBIDDEN_KEYS:
                return True
            if any(sub in lower for sub in FORBIDDEN_SUBSTRINGS):
                return True
            if lower in {"path", "url"} and isinstance(value, str) and value.startswith("/"):
                return True
            if _contains_forbidden_key(value, depth + 1):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _contains_forbidden_key(item, depth + 1):
                return True
    return False


def _redact_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    text = re.sub(r"/[^\s]{3,}", " /…", text)
    text = re.sub(r"[A-Za-z0-9_\-]*[0-9][A-Za-z0-9_\-]{8,}", "[redacted-token]", text)
    text = re.sub(r"[A-Za-z0-9+/]{24,}={0,2}", "[redacted-token]", text)
    if "secret" in text.lower() and "[redacted-token]" not in text:
        text = re.sub(r"secret[^\s]*", "[redacted-token]", text, flags=re.IGNORECASE)
    return text


def build_redacted_context(event: LifeEvent, state: LifeState) -> dict[str, Any]:
    raw_kind = str(event.kind.value if hasattr(event.kind, "value") else event.kind).strip().lower()
    event_kind = raw_kind if raw_kind in ALLOWED_EVENT_KINDS else "unknown"
    raw_mood = _redact_text(state.mood).lower()
    mood = raw_mood if raw_mood in ALLOWED_MOODS else "calm"
    recent = [_redact_text(t) for t in state.recent_texts[-MAX_RECENT_TEXTS:] if t and t.strip()]
    recent = [t for t in recent if t][:MAX_PROVIDER_REDACTED_ITEMS]
    payload: dict[str, Any] = {
        "event_kind": event_kind,
        "event_text": _redact_text(event.text),
        "event_priority": int(event.priority),
        "mood": mood,
        "presence": bool(state.presence),
        "attention": round(float(state.attention), 3),
        "energy": round(float(state.energy), 3),
        "recent_texts": recent,
        "interaction_count": int(state.interaction_count),
    }
    if _contains_forbidden_key(payload):
        raise ValueError("redacted context contains forbidden field")
    return payload


def build_prompt(event: LifeEvent, state: LifeState) -> str:
    ctx = build_redacted_context(event, state)
    lines = [
        "You are StackChan LifeOS. Return high-level behavior intents only; never raw hardware commands.",
        f"event_kind={ctx['event_kind']}",
        f"mood={ctx['mood']} presence={ctx['presence']} attention={ctx['attention']} energy={ctx['energy']}",
    ]
    if ctx["event_text"]:
        lines.append(f"user_text: {ctx['event_text']}")
    if ctx["recent_texts"]:
        lines.append(f"recent: {' | '.join(ctx['recent_texts'])}")
    prompt = "\n".join(lines)
    if len(prompt) > MAX_PROVIDER_CONTEXT_CHARS:
        prompt = prompt[:MAX_PROVIDER_CONTEXT_CHARS]
    if _contains_forbidden_key({"prompt": prompt}):
        raise ValueError("prompt contains forbidden field")
    return prompt


def validate_outbound_payload(payload: dict[str, Any]) -> None:
    if _contains_forbidden_key(payload):
        raise ValueError("outbound payload contains forbidden field")
    encoded = str(payload)
    if len(encoded) > MAX_PROVIDER_CONTEXT_CHARS * 2:
        raise ValueError("outbound payload exceeds size budget")
