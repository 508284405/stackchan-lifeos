"""Map internal mood to semantic expression intents (no hardware fields)."""

from __future__ import annotations

from .models import BehaviorIntent

_MOOD_EXPRESSIONS: dict[str, BehaviorIntent] = {
    "calm": BehaviorIntent(name="idle", reason="calm mood", priority=5, intensity=0.25),
    "happy": BehaviorIntent(name="happy", reason="happy mood", priority=20, intensity=0.6),
    "curious": BehaviorIntent(name="look_at_person", reason="curious mood", priority=25, intensity=0.45),
    "sleepy": BehaviorIntent(name="sleep", reason="sleepy mood", priority=15, intensity=0.35),
    "alert": BehaviorIntent(name="still", reason="alert mood", priority=30, intensity=0.5),
    "sad": BehaviorIntent(name="idle", reason="sad mood", priority=8, intensity=0.2),
}


def mood_to_expression(mood: str) -> BehaviorIntent:
    normalized = str(mood).strip().lower()
    base = _MOOD_EXPRESSIONS.get(normalized, _MOOD_EXPRESSIONS["calm"])
    # The returned contract is a semantic BehaviorIntent; hardware projection
    # remains the responsibility of the existing dispatch/safety layers.
    return base.model_copy(update={"reason": f"{normalized or 'calm'} expression"})
