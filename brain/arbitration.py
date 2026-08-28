"""Deterministic policy for selecting one safe semantic behaviour."""

from __future__ import annotations

from typing import List, Optional
from .models import BehaviorIntent, LifeEvent


def arbitrate(intents: List[BehaviorIntent], event: LifeEvent) -> Optional[BehaviorIntent]:
    if not intents:
        return None
    # Explicit event priority dominates provider preference; safety is always
    # allowed to interrupt an existing behaviour.
    return max(intents, key=lambda item: (event.priority if event.kind == "safety" else 0) + item.priority)
