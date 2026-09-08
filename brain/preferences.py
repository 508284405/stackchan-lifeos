"""User preferences for proactive behavior and quiet hours."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class QuietHours:
    """Local-time quiet window; spans midnight when start > end."""

    start: time = time(22, 0)
    end: time = time(7, 0)

    def contains(self, moment: datetime) -> bool:
        local = moment.time()
        if self.start <= self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


@dataclass
class UserPreferences:
    proactive_enabled: bool = True
    quiet_hours: QuietHours = QuietHours()

    def __post_init__(self) -> None:
        if not isinstance(self.proactive_enabled, bool):
            raise TypeError("proactive_enabled must be a bool")
        if not isinstance(self.quiet_hours, QuietHours):
            raise TypeError("quiet_hours must be QuietHours")

    def is_quiet(self, moment: datetime) -> bool:
        return self.quiet_hours.contains(moment)
