"""Lightweight run correlation — no prompt or token logging."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any
from uuid import uuid4


@dataclass
class MetricEvent:
    run_id: str
    thread_id: str
    event_id: str
    node: str
    at: datetime
    device_id: str = ""
    provider_request_id: str | None = None
    latency_ms: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class MetricsRecorder:
    def __init__(self):
        self._events: list[MetricEvent] = []

    def record(
        self,
        *,
        run_id: str,
        thread_id: str,
        event_id: str,
        node: str,
        device_id: str = "",
        provider_request_id: str | None = None,
        latency_ms: float | None = None,
        **extra: Any,
    ) -> MetricEvent:
        if latency_ms is not None and (not math.isfinite(float(latency_ms)) or latency_ms < 0):
            raise ValueError("latency_ms must be finite and non-negative")
        evt = MetricEvent(
            run_id=run_id,
            thread_id=thread_id,
            event_id=event_id,
            node=node,
            at=datetime.now(timezone.utc),
            device_id=device_id,
            provider_request_id=provider_request_id,
            latency_ms=float(latency_ms) if latency_ms is not None else None,
            extra=dict(extra),
        )
        self._events.append(evt)
        return evt

    def events(self) -> list[MetricEvent]:
        return list(self._events)

    def for_run(self, run_id: str) -> list[MetricEvent]:
        return [e for e in self._events if e.run_id == run_id]

    def summary(self, *, node: str | None = None) -> dict[str, Any]:
        """Return bounded latency/error counters without prompt or token data."""
        events = [e for e in self._events if node is None or e.node == node]
        values = sorted(e.latency_ms for e in events if e.latency_ms is not None)

        def percentile(rank: float) -> float | None:
            if not values:
                return None
            index = min(len(values) - 1, max(0, math.ceil(rank * len(values)) - 1))
            return round(float(values[index]), 3)

        errors: dict[str, int] = {}
        usage_totals: dict[str, int] = {}
        for event in events:
            category = event.extra.get("provider_error_category")
            if isinstance(category, str):
                errors[category] = errors.get(category, 0) + 1
            usage = event.extra.get("usage")
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage_totals[key[:32]] = usage_totals.get(key[:32], 0) + value
        return {
            "count": len(events),
            "latency_ms": {"p50": percentile(0.50), "p95": percentile(0.95), "p99": percentile(0.99)},
            "provider_errors": errors,
            "usage": usage_totals,
        }


def new_run_id() -> str:
    return str(uuid4())
