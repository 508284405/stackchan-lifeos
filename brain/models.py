"""Stable domain contracts shared by the cognitive graph and transports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


PROVIDER_CONTEXT_SCHEMA_VERSION = "sub2api.v1"
INTENT_PLAN_SCHEMA_VERSION = "intent.v1"
MAX_PROVIDER_CONTEXT_CHARS = 4_096
MAX_PROVIDER_REDACTED_ITEMS = 16
MAX_PROVIDER_TOOLS = 4


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


class ProviderErrorCategory(str, Enum):
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_RESPONSE = "invalid_response"
    POLICY_BLOCKED = "policy_blocked"


class ProviderConfig(BaseModel):
    """Deployment-time provider configuration with secret-safe dumping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = Field(min_length=1, repr=False, exclude=True)
    connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    request_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    tls_verify: bool = True
    allow_http_localhost: bool = False

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized.lower().endswith("/v1"):
            normalized = normalized[:-3].rstrip("/")
        if not normalized:
            raise ValueError("base_url is required")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model is required")
        return normalized

    @field_validator("api_key")
    @classmethod
    def require_api_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_key is required")
        return value

    @field_validator("connect_timeout_seconds", "request_timeout_seconds")
    @classmethod
    def finite_timeout(cls, value: float) -> float:
        import math
        if not math.isfinite(value):
            raise ValueError("timeout must be finite")
        return value

    def safe_dump(self) -> dict[str, Any]:
        return self.model_dump(exclude={"api_key"})


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = PROVIDER_CONTEXT_SCHEMA_VERSION
    run_id: str = Field(min_length=1, max_length=96)
    thread_id: str = Field(min_length=1, max_length=96)
    event_id: str = Field(min_length=1, max_length=96)
    device_id: str = Field(min_length=1, max_length=64)
    event_kind: str = Field(min_length=1, max_length=32)
    context: dict[str, Any] = Field(default_factory=dict)
    prompt: str = Field(min_length=1, max_length=MAX_PROVIDER_CONTEXT_CHARS)
    tools_allowed: bool = False


class _ProviderErrorData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ProviderErrorCategory
    message: str = Field(min_length=1, max_length=256)
    retryable: bool = False
    status_code: Optional[int] = None
    request_id: Optional[str] = None


class ProviderError(Exception):
    category: ProviderErrorCategory
    message: str
    retryable: bool = False
    status_code: Optional[int] = None
    request_id: Optional[str] = None

    def __init__(
        self,
        category: ProviderErrorCategory | str,
        message: str,
        retryable: bool = False,
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if kwargs:
            # allow dict-style construction via model_validate fallback
            data = _ProviderErrorData.model_validate(
                {"category": category, "message": message, "retryable": retryable, "status_code": status_code, "request_id": request_id, **kwargs}
            )
            category, message, retryable, status_code, request_id = data.category, data.message, data.retryable, data.status_code, data.request_id
        else:
            data = _ProviderErrorData(
                category=category,  # type: ignore[arg-type]
                message=message,
                retryable=retryable,
                status_code=status_code,
                request_id=request_id,
            )
        super().__init__(message)
        self.category = data.category
        self.message = data.message
        self.retryable = data.retryable
        self.status_code = data.status_code
        self.request_id = data.request_id
        self._data = data

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> "ProviderError":
        if isinstance(obj, cls):
            return obj
        data = _ProviderErrorData.model_validate(obj, *args, **kwargs)
        return cls(
            category=data.category,
            message=data.message,
            retryable=data.retryable,
            status_code=data.status_code,
            request_id=data.request_id,
        )

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._data.model_dump(*args, **kwargs)

    def __repr__(self) -> str:
        return f"ProviderError(category={self.category!r}, message={self.message!r}, retryable={self.retryable!r}, status_code={self.status_code!r})"

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> Any:
        from pydantic_core import core_schema

        def _validate(value: Any) -> "ProviderError":
            if isinstance(value, cls):
                return value
            data = _ProviderErrorData.model_validate(value)
            return cls(
                category=data.category,
                message=data.message,
                retryable=data.retryable,
                status_code=data.status_code,
                request_id=data.request_id,
            )

        def _serialize(value: "ProviderError") -> dict[str, Any]:
            return value.model_dump()

        return core_schema.no_info_after_validator_function(
            _validate,
            core_schema.any_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                _serialize, info_arg=False, return_schema=core_schema.dict_schema()
            ),
        )


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_id: str = Field(min_length=1, max_length=128)
    schema_version: str = INTENT_PLAN_SCHEMA_VERSION
    latency_ms: float = Field(ge=0.0)
    usage: dict[str, int] = Field(default_factory=dict)
    # Keep this compatibility slot private to callers; untrusted upstream
    # response bodies must never be serialized into a checkpoint or log.
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("latency_ms")
    @classmethod
    def finite_latency(cls, value: float) -> float:
        import math
        if not math.isfinite(value):
            raise ValueError("latency_ms must be finite")
        return value

    def safe_dump(self) -> dict[str, Any]:
        return self.model_dump(exclude={"raw"})


class ToolIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=256)
    args: dict[str, Any] = Field(default_factory=dict)


class IntentPlan(BaseModel):
    """Validated provider output and the request used to obtain it."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = INTENT_PLAN_SCHEMA_VERSION
    request: ProviderRequest
    intents: list["BehaviorIntent"] = Field(default_factory=list)
    tool_intents: list[ToolIntent] = Field(default_factory=list)
    result: Optional[ProviderResult] = None
    error: Optional[ProviderError] = None
    approval_required: bool = False

    @field_validator("intents")
    @classmethod
    def clamp_intents(cls, value: list["BehaviorIntent"]) -> list["BehaviorIntent"]:
        if len(value) > MAX_PROVIDER_TOOLS:
            raise ValueError(f"provider may return at most {MAX_PROVIDER_TOOLS} intents")
        return value


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

    name: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=256)
    priority: int = Field(default=0, ge=0, le=100)
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    speech: Optional[str] = Field(default=None, max_length=512)
    ttl_seconds: float = Field(default=8.0, gt=0.0, le=300.0)
    interruptible: bool = True

    @field_validator("intensity", "ttl_seconds")
    @classmethod
    def finite_numeric_fields(cls, value: float) -> float:
        import math
        if not math.isfinite(value):
            raise ValueError("intent numeric fields must be finite")
        return value


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
