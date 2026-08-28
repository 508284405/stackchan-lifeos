"""Stable domain contracts shared by the cognitive graph and transports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventKind(str, Enum):
    USER = "user"
    TOUCH = "touch"
    PRESENCE = "presence"
    VISION = "vision"
    MOTION = "motion"
    TIMER = "timer"
    SYSTEM = "system"


class LifeEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Union[EventKind, str]
    text: Optional[str] = None
    value: Any = None
    priority: int = Field(default=0, ge=0, le=100)
    timestamp: datetime = Field(default_factory=utc_now)
    source: str = "unknown"

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value else value


class BehaviorIntent(BaseModel):
    """Semantic output; never raw PWM, servo positions, or frame data."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str = ""
    priority: int = Field(default=0, ge=0, le=100)
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    speech: Optional[str] = None
    ttl_seconds: float = Field(default=8.0, gt=0.0, le=300.0)
    interruptible: bool = True


class LifeState(BaseModel):
    """Persistent, bounded internal state for one StackChan instance."""

    model_config = ConfigDict(validate_assignment=True)

    device_id: str = "stackchan-local"
    mood: str = "calm"
    energy: float = Field(default=0.8, ge=0.0, le=1.0)
    attention: float = Field(default=0.0, ge=0.0, le=1.0)
    presence: bool = False
    asleep: bool = False
    interaction_count: int = Field(default=0, ge=0)
    last_event_at: Optional[datetime] = None
    last_response_at: Optional[datetime] = None
    last_event_id: Optional[str] = None
    active_behavior: Optional[BehaviorIntent] = None
    recent_event_ids: list[str] = Field(default_factory=list)
    recent_texts: list[str] = Field(default_factory=list)

    def apply(self, event: LifeEvent) -> "LifeState":
        """Apply a meaningful event and return a new validated state."""
        values = self.model_dump()
        values.update(last_event_at=event.timestamp, last_event_id=event.id)
        ids = [*self.recent_event_ids, event.id][-32:]
        values["recent_event_ids"] = ids
        if event.kind in (EventKind.USER, EventKind.TOUCH):
            values["attention"] = min(1.0, self.attention + 0.35)
            values["energy"] = min(1.0, self.energy + 0.03)
            values["interaction_count"] = self.interaction_count + 1
            values["asleep"] = False
            if event.kind == EventKind.TOUCH:
                values["mood"] = "happy"
        elif event.kind == EventKind.PRESENCE:
            values["presence"] = bool(event.value)
            values["attention"] = max(self.attention, 0.35 if event.value else 0.0)
        elif event.kind == EventKind.MOTION:
            values["attention"] = min(1.0, self.attention + 0.15)
            values["energy"] = max(0.0, self.energy - 0.02)
        elif event.kind == EventKind.TIMER:
            values["attention"] = max(0.0, self.attention - 0.08)
            values["energy"] = max(0.0, self.energy - 0.01)
        if event.text:
            values["recent_texts"] = [*self.recent_texts, event.text][-16:]
        return type(self)(**values)
