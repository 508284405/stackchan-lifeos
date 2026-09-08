"""Local policy gate — provider output never escalates privilege."""

from __future__ import annotations

from typing import Any

from .models import BehaviorIntent, IntentPlan, ProviderError, ProviderErrorCategory, ToolIntent

ALLOWED_INTENTS = frozenset(
    {
        "greet",
        "happy",
        "look_at_person",
        "dizzy",
        "idle",
        "sleep",
        "still",
        "blink",
        "small_nod",
        "head_tilt",
        "listen",
        "thinking",
        "speaking",
        "wake",
    }
)

FORBIDDEN_INTENT_FIELDS = frozenset({"priority_override", "authorization", "maintainer", "local_confirmation", "raw", "pwm", "gpio", "angle", "shell", "url", "path"})
MAX_INTENTS = 4
MAX_TTL = 30.0
# Only a local, read-only memory lookup is enabled in this phase. Anything
# with external I/O, writes, shell access, or device access remains unregistered.
ALLOWED_TOOL_INTENTS = frozenset({"memory.recall"})


def validate_intents(intents: list[BehaviorIntent]) -> list[BehaviorIntent]:
    if len(intents) > MAX_INTENTS:
        raise ValueError(f"too many intents: {len(intents)} > {MAX_INTENTS}")
    validated: list[BehaviorIntent] = []
    for intent in intents:
        if intent.name not in ALLOWED_INTENTS:
            raise ValueError(f"intent not registered: {intent.name}")
        extra = set(intent.model_fields_set) - {"name", "reason", "priority", "intensity", "speech", "ttl_seconds", "interruptible"}
        if extra:
            raise ValueError(f"intent carries forbidden field: {extra}")
        if intent.priority > 80:
            raise ValueError("provider must not claim high priority")
        if intent.ttl_seconds > MAX_TTL:
            raise ValueError("ttl exceeds policy limit")
        validated.append(intent)
    return validated


def validate_provider_error(error: ProviderError) -> ProviderError:
    if error.category not in ProviderErrorCategory:
        raise ValueError(f"unknown error category: {error.category}")
    return error


def validate_tool_intents(tool_intents: list[ToolIntent], *, allowed_tools: frozenset[str] = ALLOWED_TOOL_INTENTS) -> list[ToolIntent]:
    """Validate the local tool boundary; no remote tools are enabled in P2."""
    if len(tool_intents) > MAX_INTENTS:
        raise ValueError(f"too many tool intents: {len(tool_intents)} > {MAX_INTENTS}")
    for intent in tool_intents:
        if intent.name not in allowed_tools:
            raise ValueError(f"tool intent not registered: {intent.name}")
    return list(tool_intents)


def validate_intent_plan(plan: IntentPlan) -> IntentPlan:
    """Apply every local policy to a provider plan before any projection."""
    if not isinstance(plan, IntentPlan):
        raise ValueError("expected IntentPlan")
    from .redaction import validate_outbound_payload

    validate_outbound_payload(plan.request.context)
    validate_intents(plan.intents)
    validate_tool_intents(plan.tool_intents)
    if plan.error is not None:
        validate_provider_error(plan.error)
    if plan.result is None and plan.error is None:
        raise ValueError("intent plan must contain a result or provider error")
    return plan


def classify_for_dispatch(intents: list[BehaviorIntent], error: ProviderError | None) -> dict[str, Any]:
    if error is not None:
        return {"degraded": True, "reason": error.category.value}
    try:
        validate_intents(intents)
    except ValueError as exc:
        return {"degraded": True, "reason": f"policy_blocked: {exc}"}
    return {"degraded": False}
