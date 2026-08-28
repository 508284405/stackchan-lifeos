"""Salience filtering before cognitive work is scheduled."""

from __future__ import annotations

from .models import EventKind, LifeEvent, LifeState


def semantic_gate(event: LifeEvent, state: LifeState) -> bool:
    """Return whether an event deserves a cognition turn.

    Safety/system and explicit user/touch events always pass. Repeated noisy
    sensor observations are suppressed by id or stable text; low-value timers
    are ignored while the device is already idle.
    """
    if event.id in state.recent_event_ids:
        return False
    if event.kind in (EventKind.USER, EventKind.TOUCH, "safety", "system"):
        return True
    if event.kind == EventKind.TIMER:
        return state.attention > 0.05 or state.energy < 0.2 or state.presence
    if event.kind == EventKind.PRESENCE:
        return bool(event.value) != state.presence
    if event.text and event.text in state.recent_texts:
        return False
    return event.priority >= 20 or event.value is not None
