"""Stable presence target selection with hold and loss timeout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import math
from typing import Optional


class TargetPhase(str, Enum):
    NONE = "none"
    TRACKED = "tracked"
    HOLDING = "holding"
    LOST = "lost"


@dataclass
class TargetSelector:
    """Tracks one presence target; stops search after loss timeout."""

    hold_seconds: float = 3.0
    loss_timeout_seconds: float = 8.0
    phase: TargetPhase = TargetPhase.NONE
    last_seen_at: Optional[datetime] = None
    loss_started_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0
            for value in (self.hold_seconds, self.loss_timeout_seconds)
        ):
            raise ValueError("target hold and loss timeouts must be non-negative")

    def update(self, presence: bool, now: datetime) -> TargetPhase:
        if presence:
            self.phase = TargetPhase.TRACKED
            self.last_seen_at = now
            self.loss_started_at = None
            return self.phase

        if self.phase == TargetPhase.TRACKED:
            self.phase = TargetPhase.HOLDING
            self.loss_started_at = now
            return self.phase

        if self.phase == TargetPhase.HOLDING and self.loss_started_at is not None:
            held = (now - self.loss_started_at).total_seconds()
            if held >= self.hold_seconds + self.loss_timeout_seconds:
                self.phase = TargetPhase.LOST
        return self.phase

    def tick(self, now: datetime) -> TargetPhase:
        """Advance hold/loss timers without a new observation."""
        if self.phase == TargetPhase.HOLDING and self.loss_started_at is not None:
            held = (now - self.loss_started_at).total_seconds()
            if held >= self.hold_seconds + self.loss_timeout_seconds:
                self.phase = TargetPhase.LOST
        return self.phase

    def loss_seconds(self) -> float:
        return self.hold_seconds + self.loss_timeout_seconds

    def should_search(self) -> bool:
        return self.phase in (TargetPhase.TRACKED, TargetPhase.HOLDING)

    def allows_look_at_person(self) -> bool:
        return self.should_search()

    def search_state(self) -> str:
        """Stable semantic status for callers that must avoid search after loss."""
        return "searching" if self.should_search() else "stopped"

    def snapshot(self) -> dict[str, object]:
        return {
            "hold_seconds": self.hold_seconds,
            "loss_timeout_seconds": self.loss_timeout_seconds,
            "phase": self.phase.value,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "loss_started_at": self.loss_started_at.isoformat() if self.loss_started_at else None,
        }

    @classmethod
    def from_snapshot(cls, snapshot: object) -> "TargetSelector":
        if not isinstance(snapshot, dict):
            return cls()
        selector = cls(
            hold_seconds=float(snapshot.get("hold_seconds", 3.0)),
            loss_timeout_seconds=float(snapshot.get("loss_timeout_seconds", 8.0)),
        )
        try:
            selector.phase = TargetPhase(str(snapshot.get("phase", TargetPhase.NONE.value)))
        except ValueError:
            selector.phase = TargetPhase.NONE
        for field_name in ("last_seen_at", "loss_started_at"):
            raw = snapshot.get(field_name)
            if isinstance(raw, str):
                try:
                    setattr(selector, field_name, datetime.fromisoformat(raw))
                except ValueError:
                    setattr(selector, field_name, None)
        return selector
