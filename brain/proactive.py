"""Caps for limited proactive behaviors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
from typing import Optional

from .preferences import UserPreferences


@dataclass
class ProactivePolicy:
    max_per_hour: int = 6
    cooldown_seconds: float = 300.0
    max_active_seconds: float = 12.0
    _history: list[datetime] = field(default_factory=list)
    last_proactive_at: Optional[datetime] = None
    active_started_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.max_per_hour, bool) or not isinstance(self.max_per_hour, int) or self.max_per_hour < 0:
            raise ValueError("max_per_hour must be a non-negative integer")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0
            for value in (self.cooldown_seconds, self.max_active_seconds)
        ):
            raise ValueError("proactive time limits must be non-negative")

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        self._history = [t for t in self._history if t >= cutoff]

    def can_propose(self, now: datetime, prefs: UserPreferences) -> tuple[bool, str]:
        if not prefs.proactive_enabled:
            return False, "proactive_disabled"
        if prefs.is_quiet(now):
            return False, "quiet_hours"
        self._prune(now)
        if len(self._history) >= self.max_per_hour:
            return False, "hourly_cap"
        if self.last_proactive_at is not None:
            elapsed = (now - self.last_proactive_at).total_seconds()
            if elapsed < self.cooldown_seconds:
                return False, "cooldown"
        return True, ""

    def can_emit(self, now: datetime, prefs: UserPreferences) -> tuple[bool, str]:
        """Single policy entry point for timers and other proactive triggers."""
        if self.is_active_blocking(now):
            return False, "active_duration"
        return self.can_propose(now, prefs)

    def is_active_blocking(self, now: datetime) -> bool:
        if self.active_started_at is None:
            return False
        return (now - self.active_started_at).total_seconds() < self.max_active_seconds

    def record_proactive(self, now: datetime) -> None:
        self._prune(now)
        self._history.append(now)
        self.last_proactive_at = now
        self.active_started_at = now

    def clear_active(self) -> None:
        self.active_started_at = None

    def is_proactive_intent(self, name: str) -> bool:
        return name in {"idle", "blink", "small_nod", "head_tilt", "wake"}

    def snapshot(self) -> dict[str, object]:
        return {
            "max_per_hour": self.max_per_hour,
            "cooldown_seconds": self.cooldown_seconds,
            "max_active_seconds": self.max_active_seconds,
            "history": [item.isoformat() for item in self._history],
            "last_proactive_at": self.last_proactive_at.isoformat() if self.last_proactive_at else None,
            "active_started_at": self.active_started_at.isoformat() if self.active_started_at else None,
        }

    @classmethod
    def from_snapshot(cls, snapshot: object) -> "ProactivePolicy":
        if not isinstance(snapshot, dict):
            return cls()
        policy = cls(
            max_per_hour=int(snapshot.get("max_per_hour", 6)),
            cooldown_seconds=float(snapshot.get("cooldown_seconds", 300.0)),
            max_active_seconds=float(snapshot.get("max_active_seconds", 12.0)),
        )
        history = snapshot.get("history", [])
        if isinstance(history, list):
            for raw in history:
                if isinstance(raw, str):
                    try:
                        policy._history.append(datetime.fromisoformat(raw))
                    except ValueError:
                        continue
        for field_name in ("last_proactive_at", "active_started_at"):
            raw = snapshot.get(field_name)
            if isinstance(raw, str):
                try:
                    setattr(policy, field_name, datetime.fromisoformat(raw))
                except ValueError:
                    setattr(policy, field_name, None)
        return policy
