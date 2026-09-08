"""Allowlisted high-level behavior and speech payloads for command.intent."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import ValidationError


MAX_BEHAVIORS = 4
MAX_BEHAVIOR_DURATION_MS = 30_000
MAX_SPEECH_CHARS = 1_000
ALLOWED_VOICES = frozenset({"default", "calm", "bright"})
ALLOWED_BEHAVIORS = frozenset(
    {
        "still",
        "blink",
        "small_nod",
        "head_tilt",
        "greet",
        "listen",
        "thinking",
        "speaking",
        "sleep",
        "wake",
    }
)


@dataclass(frozen=True)
class BehaviorSpec:
    name: str
    intensity: float
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "intensity": self.intensity,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class SpeechSpec:
    text: str
    voice: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "voice": self.voice}


def validate_behavior(name: str, intensity: float, duration_ms: int) -> BehaviorSpec:
    if name not in ALLOWED_BEHAVIORS:
        raise ValidationError(f"behavior is not registered: {name}")
    if (
        isinstance(intensity, bool)
        or not isinstance(intensity, (int, float))
        or not math.isfinite(intensity)
        or not 0 <= intensity <= 1
    ):
        raise ValidationError("behavior intensity must be a finite number in [0, 1]")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or not 1 <= duration_ms <= MAX_BEHAVIOR_DURATION_MS
    ):
        raise ValidationError(
            f"behavior duration_ms must be between 1 and {MAX_BEHAVIOR_DURATION_MS}"
        )
    return BehaviorSpec(name=name, intensity=float(intensity), duration_ms=duration_ms)


def validate_speech(text: str, voice: str = "default") -> SpeechSpec:
    if not isinstance(text, str):
        raise ValidationError("speech text must be a string")
    normalized = text.strip()
    if not normalized or len(normalized) > MAX_SPEECH_CHARS:
        raise ValidationError(f"speech text must contain 1..{MAX_SPEECH_CHARS} characters")
    if voice not in ALLOWED_VOICES:
        raise ValidationError(f"voice is not registered: {voice}")
    return SpeechSpec(text=normalized, voice=voice)


def build_intent_payload(
    *,
    run_id: str,
    expires_at_ms: int,
    source: str,
    behaviors: Iterable[BehaviorSpec] = (),
    speech: SpeechSpec | None = None,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or not run_id or len(run_id) > 96:
        raise ValidationError("intent run_id must be a bounded non-empty string")
    if not isinstance(expires_at_ms, int) or isinstance(expires_at_ms, bool) or expires_at_ms < 0:
        raise ValidationError("intent expires_at_ms must be a non-negative integer")
    if source not in {"web", "agent", "batch", "system"}:
        raise ValidationError(f"intent source is not registered: {source}")
    behavior_values = list(behaviors)
    if len(behavior_values) > MAX_BEHAVIORS:
        raise ValidationError(f"an intent may contain at most {MAX_BEHAVIORS} behaviors")
    if not behavior_values and speech is None:
        raise ValidationError("intent must contain a behavior or speech")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "expires_at_ms": expires_at_ms,
        "source": source,
        "behaviors": [behavior.to_dict() for behavior in behavior_values],
    }
    if speech is not None:
        payload["speech"] = speech.to_dict()
    return payload
