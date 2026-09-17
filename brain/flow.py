"""LangGraph cognitive cycle with recall, dispatch, checkpoint, and Phase 3 policy."""

from __future__ import annotations

from time import monotonic
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from .arbitration import arbitrate
from .checkpoint import Checkpoint, MemoryCheckpointer, GRAPH_VERSION, PROVIDER_CONTRACT_VERSION
from .dispatch import LocalToolExecutor, project_command, validate_projection
from .emotion import mood_to_expression
from .gate import semantic_gate
from .interrupt import ApprovalRegistry, ApprovalStatus
from .metrics import MetricsRecorder, new_run_id
from .models import BehaviorIntent, EventKind, LifeEvent, LifeState, ProviderError, ToolIntent, utc_now
from .preferences import UserPreferences
from .proactive import ProactivePolicy
from .provider import DeterministicInferenceProvider, InferenceProvider
from .redaction import _redact_text, build_redacted_context
from .store import LifeOSStore
from .target import TargetSelector
from .validator import classify_for_dispatch, validate_tool_intents

SPEECH_APPROVAL_THRESHOLD = 40


class CognitiveGraphState(TypedDict, total=False):
    life_state: LifeState
    event: LifeEvent
    accepted: bool
    redacted_context: dict[str, Any]
    recalled_memories: list[dict[str, Any]]
    candidates: list[BehaviorIntent]
    tool_intents: list[ToolIntent]
    tool_results: list[dict[str, Any]]
    intent: Optional[BehaviorIntent]
    degraded: bool
    execution_mode: str
    degrade_reason: str
    provider_error: Optional[ProviderError]
    awaiting_approval: bool
    approval_audit_id: Optional[str]
    approval_status: str
    checkpoint_recovery: str
    dispatched: bool
    command_envelope: Optional[dict[str, Any]]
    outbox_duplicate: bool
    run_id: str
    thread_id: str
    preferences: UserPreferences
    proactive_policy: ProactivePolicy
    target_selector: TargetSelector
    proactive_blocked: str
    device_connected: bool
    store: LifeOSStore


def _local_reflex_intent(state: LifeState) -> BehaviorIntent:
    return mood_to_expression(state.mood)


def _filter_disconnect_unsafe(intents: list[BehaviorIntent], state: LifeState | None = None) -> list[BehaviorIntent]:
    safe: list[BehaviorIntent] = []
    for intent in intents:
        if intent.name in {"look_at_person", "greet", "speaking"}:
            continue
        if intent.speech:
            continue
        safe.append(intent)
    return safe or [_local_reflex_intent(state or LifeState())]


def _without_target_search(intents: list[BehaviorIntent], selector: TargetSelector) -> list[BehaviorIntent]:
    """Remove target-seeking expressions whenever a target is unavailable."""
    if selector.allows_look_at_person():
        return intents
    safe = [intent for intent in intents if intent.name != "look_at_person"]
    return safe or [BehaviorIntent(name="still", reason="target unavailable", priority=10, intensity=0.2)]


def _needs_speech_approval(intent: BehaviorIntent | None) -> bool:
    return bool(intent and intent.name == "speaking" and intent.speech)


def _checkpoint_event(event: LifeEvent) -> dict[str, Any]:
    """Keep only the identifiers needed for explicit approval/replay.

    Event values and extra fields may contain raw media, credentials, or other
    untrusted data. They are deliberately not part of a recovery snapshot.
    """
    raw_kind = str(event.kind.value if hasattr(event.kind, "value") else event.kind).lower()
    kind = raw_kind if raw_kind in {item.value for item in EventKind} else "system"
    return {"id": event.id, "kind": kind}


def _checkpoint_life_state(state: LifeState) -> dict[str, Any]:
    """Persist only bounded state required to continue safely after restart."""
    return {
        "device_id": state.device_id,
        "mood": state.mood,
        "energy": state.energy,
        "attention": state.attention,
        "presence": state.presence,
        "asleep": state.asleep,
        "interaction_count": state.interaction_count,
        "last_event_at": state.last_event_at.isoformat() if state.last_event_at else None,
        "last_response_at": state.last_response_at.isoformat() if state.last_response_at else None,
        "last_event_id": state.last_event_id,
        "recent_event_ids": list(state.recent_event_ids[-32:]),
        "recent_texts": [],
        "active_behavior": None,
    }


def _checkpoint_intent(intent: BehaviorIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    data = intent.model_dump(mode="json")
    data["reason"] = _redact_text(data.get("reason"))
    if data.get("speech"):
        data["speech"] = _redact_text(data["speech"])
    return data


def build_cognitive_graph(
    provider: Optional[InferenceProvider] = None,
    *,
    checkpointer: MemoryCheckpointer | None = None,
    approvals: ApprovalRegistry | None = None,
    metrics: MetricsRecorder | None = None,
    tool_executor: LocalToolExecutor | None = None,
):
    provider = provider or DeterministicInferenceProvider()
    checkpointer = checkpointer or MemoryCheckpointer()
    approvals = approvals or ApprovalRegistry()
    metrics = metrics or MetricsRecorder()

    async def gate_node(data: CognitiveGraphState):
        state, event = data["life_state"], data["event"]
        return {"accepted": semantic_gate(event, state)}

    def route_after_gate(data: CognitiveGraphState) -> str:
        return "context" if data.get("accepted") else "checkpoint"

    def context_node(data: CognitiveGraphState):
        event, state = data["event"], data["life_state"]
        selector = data.get("target_selector") or TargetSelector()
        state = state.apply(event)
        if event.kind == EventKind.PRESENCE:
            selector.update(bool(event.value), event.timestamp)
        else:
            selector.tick(event.timestamp)
        return {
            "life_state": state,
            "target_selector": selector,
            "redacted_context": build_redacted_context(event, state),
        }

    def recall_node(data: CognitiveGraphState):
        store = data.get("store")
        if store is None:
            return {"recalled_memories": []}
        event = data["event"]
        query = (event.text or "").strip()
        return {"recalled_memories": store.recall(("lifeos", "memory"), query=query)}

    async def decide_node(data: CognitiveGraphState):
        connected = data.get("device_connected", True)
        selector = data.get("target_selector") or TargetSelector()
        state = data["life_state"]
        event = data["event"]

        if not connected:
            return {
                "candidates": _filter_disconnect_unsafe([_local_reflex_intent(state)], state),
                "provider_error": None,
                "degraded": True,
                "degrade_reason": "device_disconnected",
            }

        if event.kind == EventKind.PRESENCE and not selector.allows_look_at_person():
            return {
                "candidates": [BehaviorIntent(name="still", reason="target lost", priority=10, intensity=0.2)],
                "provider_error": None,
            }

        started = monotonic()
        try:
            bind_thread = getattr(provider, "bind_thread", None)
            if callable(bind_thread):
                bind_thread(data["thread_id"])
            candidates = await provider.decide(event, state)
            provider_result = getattr(provider, "last_result", None)
            tool_intents = list(getattr(provider, "last_tool_intents", []))
            metrics.record(
                run_id=data["run_id"],
                thread_id=data["thread_id"],
                event_id=event.id,
                node="provider",
                device_id=state.device_id,
                latency_ms=(monotonic() - started) * 1000,
                provider_request_id=(provider_result.response_id if provider_result is not None else None),
                usage=(provider_result.usage if provider_result is not None else {}),
            )
            candidates = _without_target_search(candidates, selector)
            return {"candidates": candidates, "tool_intents": tool_intents, "provider_error": None}
        except ProviderError as exc:
            metrics.record(
                run_id=data["run_id"],
                thread_id=data["thread_id"],
                event_id=event.id,
                node="provider",
                device_id=state.device_id,
                latency_ms=(monotonic() - started) * 1000,
                provider_error_category=exc.category.value,
            )
            return {"candidates": [], "provider_error": exc}
        except Exception as exc:
            metrics.record(
                run_id=data["run_id"],
                thread_id=data["thread_id"],
                event_id=event.id,
                node="provider",
                device_id=state.device_id,
                latency_ms=(monotonic() - started) * 1000,
                provider_error_category="invalid_response",
            )
            return {
                "candidates": [],
                "provider_error": ProviderError.model_validate(
                    {"category": "invalid_response", "message": str(exc)[:200], "retryable": False}
                ),
            }

    def validate_node(data: CognitiveGraphState):
        if data.get("degraded") and data.get("degrade_reason") == "device_disconnected":
            candidates = _filter_disconnect_unsafe(data.get("candidates", []), data["life_state"])
            return {"degraded": True, "execution_mode": "DEGRADED_LOCAL", "degrade_reason": "device_disconnected", "candidates": candidates}
        error: ProviderError | None = data.get("provider_error")
        candidates: list[BehaviorIntent] = data.get("candidates", [])  # type: ignore[assignment]
        tool_intents: list[ToolIntent] = data.get("tool_intents", [])  # type: ignore[assignment]
        try:
            validate_tool_intents(tool_intents)
        except ValueError as exc:
            return {
                "degraded": True,
                "execution_mode": "DEGRADED_LOCAL",
                "degrade_reason": f"policy_blocked: {exc}",
                "candidates": [],
                "tool_results": [],
            }
        verdict = classify_for_dispatch(candidates, error)
        if verdict["degraded"]:
            return {"degraded": True, "execution_mode": "DEGRADED_LOCAL", "degrade_reason": verdict["reason"], "candidates": []}
        return {"degraded": False, "execution_mode": "ACTIVE", "degrade_reason": "", "candidates": candidates}

    def tool_execute_node(data: CognitiveGraphState):
        tool_intents = list(data.get("tool_intents", []))
        if data.get("degraded") or not tool_intents:
            return {"tool_results": []}
        if tool_executor is None:
            return {
                "degraded": True,
                "execution_mode": "DEGRADED_LOCAL",
                "degrade_reason": "tool_executor_unavailable",
                "tool_results": [],
            }
        results: list[dict[str, Any]] = []
        for index, tool_intent in enumerate(tool_intents):
            try:
                results.append(
                    tool_executor.execute(
                        tool_intent,
                        idempotency_key=f"{data['thread_id']}:{data['event'].id}:tool:{tool_intent.name}:{index}",
                    )
                )
            except Exception as exc:
                return {
                    "degraded": True,
                    "execution_mode": "DEGRADED_LOCAL",
                    "degrade_reason": f"tool_failed: {str(exc)[:160]}",
                    "tool_results": [],
                }
        return {"tool_results": results}

    def arbitrate_node(data: CognitiveGraphState):
        event = data["event"]
        state = data["life_state"]
        prefs = data.get("preferences") or UserPreferences()
        policy = data.get("proactive_policy") or ProactivePolicy()
        now = event.timestamp

        if data.get("degraded"):
            if data.get("degrade_reason") == "device_disconnected":
                chosen = _filter_disconnect_unsafe(data.get("candidates", []), state)[0]
                state = state.model_copy(update={"active_behavior": chosen, "last_response_at": utc_now()})
                return {"intent": chosen, "life_state": state, "proactive_blocked": "device_disconnected"}
            return {"intent": None, "proactive_blocked": data.get("degrade_reason", "")}

        candidates = list(data.get("candidates", []))
        selector = data.get("target_selector") or TargetSelector()
        candidates = _without_target_search(candidates, selector)
        if event.kind == EventKind.TIMER:
            allowed, reason = policy.can_emit(now, prefs)
            if not allowed:
                return {"intent": None, "life_state": state, "proactive_blocked": reason}
            candidates.append(_without_target_search([mood_to_expression(state.mood)], selector)[0])

        chosen = arbitrate(candidates, event)
        if chosen and policy.is_proactive_intent(chosen.name) and event.kind == EventKind.TIMER:
            policy.record_proactive(now)
        if chosen:
            state = state.model_copy(update={"active_behavior": chosen, "last_response_at": utc_now()})
        return {"intent": chosen, "life_state": state, "proactive_blocked": ""}

    def approval_node(data: CognitiveGraphState):
        intent = data.get("intent")
        if _needs_speech_approval(intent):
            req = approvals.request(
                thread_id=data["thread_id"],
                run_id=data["run_id"],
                tool_name=intent.name,  # type: ignore[union-attr]
                reason=intent.reason or "speech requires approval",  # type: ignore[union-attr]
            )
            return {"awaiting_approval": True, "approval_audit_id": req.audit_id, "approval_status": ApprovalStatus.PENDING.value}
        return {"awaiting_approval": False, "approval_audit_id": None, "approval_status": ""}

    def route_after_approval(data: CognitiveGraphState) -> str:
        return "checkpoint" if data.get("awaiting_approval") else "dispatch"

    def dispatch_node(data: CognitiveGraphState):
        intent = data.get("intent")
        if intent is None or data.get("degraded") or data.get("awaiting_approval"):
            return {"dispatched": False, "command_envelope": None, "outbox_duplicate": False}
        event, state = data["event"], data["life_state"]
        idempotency_key = f"{data['thread_id']}:{event.id}:{intent.name}"
        envelope = project_command(intent, event, state, idempotency_key=idempotency_key)
        validate_projection(envelope)
        entry = {"idempotency_key": idempotency_key, "envelope": envelope, "run_id": data["run_id"]}
        duplicate = not checkpointer.append_outbox(data["thread_id"], entry)
        return {"dispatched": not duplicate, "command_envelope": envelope, "outbox_duplicate": duplicate}

    def observe_node(data: CognitiveGraphState):
        metrics.record(
            run_id=data["run_id"],
            thread_id=data["thread_id"],
            event_id=data["event"].id,
            node="observe",
            device_id=data["life_state"].device_id,
            dispatched=bool(data.get("dispatched")),
            degraded=bool(data.get("degraded")),
        )
        return {}

    def checkpoint_node(data: CognitiveGraphState):
        thread_id = data["thread_id"]
        existing = checkpointer.load(thread_id)
        cp = Checkpoint(
            thread_id=thread_id,
            graph_version=GRAPH_VERSION,
            provider_contract_version=PROVIDER_CONTRACT_VERSION,
            state={
                "life_state": _checkpoint_life_state(data["life_state"]),
                "event": _checkpoint_event(data["event"]),
                "intent": _checkpoint_intent(data.get("intent")),
                "run_id": data.get("run_id"),
                "target_selector": (data.get("target_selector") or TargetSelector()).snapshot(),
                "proactive_policy": (data.get("proactive_policy") or ProactivePolicy()).snapshot(),
            },
            outbox=list(existing.outbox) if existing else [],
            pending_intent=(
                _checkpoint_intent(data.get("intent"))
                if data.get("awaiting_approval") and data.get("intent")
                else (existing.pending_intent if existing else None)
            ),
            approval_audit_id=data.get("approval_audit_id") or (existing.approval_audit_id if existing else None),
            approval_status=data.get("approval_status") or (existing.approval_status if existing else None),
        )
        checkpointer.save(cp)
        return {"dispatched": data.get("dispatched", False), "outbox_duplicate": data.get("outbox_duplicate", False)}

    graph = StateGraph(CognitiveGraphState)
    graph.add_node("gate", gate_node)
    graph.add_node("context", context_node)
    graph.add_node("recall", recall_node)
    graph.add_node("decide", decide_node)
    graph.add_node("validate", validate_node)
    graph.add_node("tool_execute", tool_execute_node)
    graph.add_node("arbitrate", arbitrate_node)
    graph.add_node("approval", approval_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("observe", observe_node)
    graph.add_node("checkpoint", checkpoint_node)
    graph.set_entry_point("gate")
    graph.add_conditional_edges("gate", route_after_gate, {"context": "context", "checkpoint": "checkpoint"})
    graph.add_edge("context", "recall")
    graph.add_edge("recall", "decide")
    graph.add_edge("decide", "validate")
    graph.add_edge("validate", "tool_execute")
    graph.add_edge("tool_execute", "arbitrate")
    graph.add_edge("arbitrate", "approval")
    graph.add_conditional_edges("approval", route_after_approval, {"dispatch": "dispatch", "checkpoint": "checkpoint"})
    graph.add_edge("dispatch", "observe")
    graph.add_edge("observe", "checkpoint")
    graph.add_edge("checkpoint", END)
    return graph.compile()


async def run_cognitive_cycle(
    event: LifeEvent,
    life_state: Optional[LifeState] = None,
    provider: Optional[InferenceProvider] = None,
    *,
    thread_id: str = "default",
    store: Optional[LifeOSStore] = None,
    memory_store: Optional[LifeOSStore] = None,
    checkpointer: Optional[MemoryCheckpointer] = None,
    metrics: Optional[MetricsRecorder] = None,
    approvals: Optional[ApprovalRegistry] = None,
    preferences: Optional[UserPreferences] = None,
    proactive_policy: Optional[ProactivePolicy] = None,
    target_selector: Optional[TargetSelector] = None,
    tool_executor: Optional[LocalToolExecutor] = None,
    device_connected: bool = True,
) -> CognitiveGraphState:
    resolved_store = store or memory_store or LifeOSStore()
    resolved_checkpointer = checkpointer or MemoryCheckpointer()
    prior = resolved_checkpointer.restore(thread_id)
    recovered = False
    if life_state is None:
        if prior is not None:
            try:
                life_state = LifeState.model_validate(prior.state.get("life_state", {}))
                recovered = True
            except Exception:
                # A corrupt/stale snapshot must not prevent a fresh safe state.
                life_state = LifeState()
        else:
            life_state = LifeState()
    resolved_target_selector = target_selector
    if resolved_target_selector is None and prior is not None:
        resolved_target_selector = TargetSelector.from_snapshot(prior.state.get("target_selector"))
    if resolved_target_selector is None:
        resolved_target_selector = TargetSelector()
    resolved_proactive_policy = proactive_policy
    if resolved_proactive_policy is None and prior is not None:
        resolved_proactive_policy = ProactivePolicy.from_snapshot(prior.state.get("proactive_policy"))
    if resolved_proactive_policy is None:
        resolved_proactive_policy = ProactivePolicy()
    resolved_tool_executor = tool_executor
    if resolved_tool_executor is None:
        resolved_tool_executor = LocalToolExecutor({"memory.recall"})
        resolved_tool_executor.register(
            "memory.recall",
            lambda args: {"memories": resolved_store.recall(("lifeos", "memory"), query=str(args.get("query", "")))},
            allowed_args={"query"},
        )
    graph = build_cognitive_graph(
        provider,
        checkpointer=resolved_checkpointer,
        approvals=approvals or ApprovalRegistry(),
        metrics=metrics or MetricsRecorder(),
        tool_executor=resolved_tool_executor,
    )
    payload: CognitiveGraphState = {
        "event": event,
        "life_state": life_state,
        "thread_id": thread_id,
        "run_id": new_run_id(),
        "preferences": preferences or UserPreferences(),
        "proactive_policy": resolved_proactive_policy,
        "target_selector": resolved_target_selector,
        "device_connected": device_connected,
        "store": resolved_store,
        "checkpoint_recovery": "restored" if recovered else "new",
    }
    result = await graph.ainvoke(payload)
    if "recalled_memories" in result:
        result["recalled"] = result["recalled_memories"]
    return result


async def replay_dispatch(
    thread_id: str,
    *,
    checkpointer: MemoryCheckpointer,
) -> CognitiveGraphState:
    """Re-attempt dispatch from checkpoint without re-invoking the provider."""
    cp = checkpointer.restore(thread_id)
    if cp is None:
        raise ValueError(f"no checkpoint for thread {thread_id}")
    raw = cp.state
    if cp.pending_intent or cp.approval_status == ApprovalStatus.PENDING.value:
        return {
            **raw,
            "thread_id": thread_id,
            "dispatched": False,
            "awaiting_approval": True,
            "approval_status": cp.approval_status or ApprovalStatus.PENDING.value,
            "outbox_duplicate": False,
        }  # type: ignore[return-value]
    if cp.approval_status in {
        ApprovalStatus.REJECTED.value,
        ApprovalStatus.TIMEOUT.value,
        ApprovalStatus.RESTARTED.value,
    }:
        return {
            **raw,
            "thread_id": thread_id,
            "dispatched": False,
            "awaiting_approval": False,
            "approval_status": cp.approval_status,
            "outbox_duplicate": False,
        }  # type: ignore[return-value]
    event = LifeEvent.model_validate(raw["event"])
    life_state = LifeState.model_validate(raw["life_state"])
    intent_raw = raw.get("intent")
    if not intent_raw:
        return dict(raw)  # type: ignore[return-value]
    intent = BehaviorIntent.model_validate(intent_raw)
    idempotency_key = f"{thread_id}:{event.id}:{intent.name}"
    existing = checkpointer.load(thread_id)
    if existing and any(e.get("idempotency_key") == idempotency_key for e in existing.outbox):
        return {
            **raw,
            "thread_id": thread_id,
            "dispatched": False,
            "outbox_duplicate": True,
            "idempotency_key": idempotency_key,
        }  # type: ignore[return-value]
    envelope = project_command(intent, event, life_state, idempotency_key=idempotency_key)
    validate_projection(envelope)
    entry = {"idempotency_key": idempotency_key, "envelope": envelope, "run_id": raw.get("run_id")}
    if not checkpointer.append_outbox(thread_id, entry):
        return {
            **raw,
            "thread_id": thread_id,
            "dispatched": False,
            "outbox_duplicate": True,
            "idempotency_key": idempotency_key,
        }  # type: ignore[return-value]
    return {
        **raw,
        "thread_id": thread_id,
        "dispatched": True,
        "outbox_duplicate": False,
        "command_envelope": envelope,
        "idempotency_key": idempotency_key,
    }  # type: ignore[return-value]


async def resume_after_approval(
    thread_id: str,
    audit_id: str,
    decision: str,
    *,
    checkpointer: Optional[MemoryCheckpointer] = None,
    approvals: Optional[ApprovalRegistry] = None,
    metrics: Optional[MetricsRecorder] = None,
) -> CognitiveGraphState:
    checkpointer = checkpointer or MemoryCheckpointer()
    approvals = approvals or ApprovalRegistry()
    metrics = metrics or MetricsRecorder()
    cp = checkpointer.restore(thread_id)
    if cp is None:
        return {"approval_status": "missing_checkpoint", "dispatched": False}

    if decision == "approve":
        req = approvals.approve(audit_id)
    elif decision == "reject":
        req = approvals.reject(audit_id)
    elif decision == "restart":
        req = approvals.restart(audit_id)
    else:
        req = approvals.timeout(audit_id)

    if req is None and cp.approval_audit_id == audit_id and cp.approval_status == ApprovalStatus.PENDING.value and cp.pending_intent:
        # A process restart loses the in-memory registry, but the checkpoint
        # still contains the non-secret approval metadata needed to resolve it.
        pending = cp.pending_intent
        req = approvals.restore_pending(
            audit_id=audit_id,
            thread_id=thread_id,
            run_id=str(cp.state.get("run_id") or "recovered-run"),
            tool_name=str(pending.get("name") or "unknown"),
            reason=str(pending.get("reason") or "approval required"),
        )
        if decision == "approve":
            req = approvals.approve(audit_id) or req
        elif decision == "reject":
            req = approvals.reject(audit_id) or req
        elif decision == "restart":
            req = approvals.restart(audit_id) or req
        else:
            req = approvals.timeout(audit_id) or req

    if req is None:
        return {"approval_status": "not_found", "audit_id": audit_id, "dispatched": False}

    life_state = LifeState.model_validate(cp.state.get("life_state", {}))
    if not cp.pending_intent:
        return {"approval_status": "missing_intent", "audit_id": req.audit_id, "dispatched": False}
    try:
        intent = BehaviorIntent.model_validate(cp.pending_intent)
    except Exception:
        return {"approval_status": "invalid_intent", "audit_id": req.audit_id, "dispatched": False}
    run_id = req.run_id

    if req.status is ApprovalStatus.APPROVED:
        event_data = cp.state.get("event", {"kind": "system", "id": req.audit_id})
        event = LifeEvent.model_validate(event_data)
        idempotency_key = f"{thread_id}:{req.audit_id}:approved"
        envelope = project_command(intent, event, life_state, idempotency_key=idempotency_key)
        validate_projection(envelope)
        entry = {"idempotency_key": idempotency_key, "envelope": envelope, "run_id": run_id}
        duplicate = not checkpointer.append_outbox(thread_id, entry)
        # append_outbox updates the stored snapshot; reload it before changing
        # approval metadata so the outbox cannot be overwritten by stale cp.
        cp = checkpointer.load(thread_id) or cp
        cp.pending_intent = None
        cp.state["intent"] = None
        cp.approval_audit_id = req.audit_id
        cp.approval_status = req.status.value
        checkpointer.save(cp)
        metrics.record(run_id=run_id, thread_id=thread_id, event_id=req.audit_id, node="observe", device_id=life_state.device_id, dispatched=not duplicate)
        return {
            "approval_status": req.status.value,
            "audit_id": req.audit_id,
            "dispatched": not duplicate,
            "command_envelope": envelope,
            "intent": intent,
            "life_state": life_state,
        }

    metrics.record(run_id=run_id, thread_id=thread_id, event_id=req.audit_id, node="observe", device_id=life_state.device_id, dispatched=False)
    cp.approval_audit_id = req.audit_id
    cp.approval_status = req.status.value
    cp.pending_intent = None
    cp.state["intent"] = None
    checkpointer.save(cp)
    return {"approval_status": req.status.value, "audit_id": req.audit_id, "dispatched": False, "life_state": life_state}
