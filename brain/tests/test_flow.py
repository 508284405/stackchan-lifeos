"""Phase 2 graph path: context, recall, dispatch, checkpoint, interrupt."""

import asyncio

import pytest

from brain.checkpoint import MemoryCheckpointer
from brain.dispatch import project_command, validate_projection
from brain.flow import replay_dispatch, resume_after_approval, run_cognitive_cycle
from brain.interrupt import ApprovalRegistry
from brain.metrics import MetricsRecorder
from brain.models import BehaviorIntent, LifeEvent, LifeState
from brain.provider import DeterministicCodexProvider
from brain.store import LifeOSStore
from brain.validator import validate_tool_intents


class SpeakingProvider:
    async def decide(self, event, state):
        return [BehaviorIntent(name="speaking", reason="needs approval", priority=50, speech="hello there")]


def test_dispatch_projection_is_semantic_only():
    intent = BehaviorIntent(name="greet", priority=60, speech="hi")
    event = LifeEvent(kind="user", text="hello", id="evt-1")
    state = LifeState(device_id="dev-1")
    envelope = project_command(intent, event, state)
    validate_projection(envelope)
    assert envelope["schema"] == "lifeos.v1"
    assert envelope["type"] == "command.intent"
    assert envelope["kind"] == "command"
    assert {"schema", "kind", "type", "event_id", "device_id", "seq", "ts_ms", "payload"}.issubset(envelope)
    assert "schema_version" not in envelope
    assert envelope["payload"]["expires_at_ms"] > envelope["payload"]["issued_at_ms"]
    assert envelope["payload"]["behavior"] == "greet"
    assert envelope["correlation_id"] == "evt-1"
    assert "angle" not in str(envelope).lower()
    assert "pwm" not in str(envelope).lower()


def test_full_graph_path_records_context_recall_and_metrics():
    store = LifeOSStore()
    store.put(("lifeos", "memory"), "greeting", {"topic": "hello"}, reason="seed", run_id="seed-run")
    metrics = MetricsRecorder()
    checkpointer = MemoryCheckpointer()
    thread_id = "thread-full"

    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind="user", text="hello", id="evt-full"),
            LifeState(),
            provider=DeterministicCodexProvider(),
            thread_id=thread_id,
            store=store,
            checkpointer=checkpointer,
            metrics=metrics,
        )
    )
    assert result["accepted"] is True
    assert result["redacted_context"]["event_kind"] == "user"
    assert isinstance(result["recalled_memories"], list)
    assert result["intent"].name == "greet"
    assert result["dispatched"] is True
    assert result["command_envelope"]["payload"]["behavior"] == "greet"
    assert any(e.node == "observe" for e in metrics.events())
    cp = checkpointer.restore(thread_id)
    assert cp is not None
    assert len(cp.outbox) == 1


def test_checkpoint_recovery_skips_duplicate_outbox():
    checkpointer = MemoryCheckpointer()
    thread_id = "thread-dedup"
    event = LifeEvent(kind="user", text="hello", id="evt-dedup")

    first = asyncio.run(
        run_cognitive_cycle(
            event,
            LifeState(),
            thread_id=thread_id,
            checkpointer=checkpointer,
        )
    )
    assert first["dispatched"] is True
    assert len(checkpointer.load(thread_id).outbox) == 1

    replay = asyncio.run(replay_dispatch(thread_id, checkpointer=checkpointer))
    assert replay["outbox_duplicate"] is True
    assert replay["dispatched"] is False
    assert len(checkpointer.load(thread_id).outbox) == 1


def test_interrupt_approve_dispatches_after_resume():
    checkpointer = MemoryCheckpointer()
    approvals = ApprovalRegistry()
    thread_id = "thread-approve"
    event = LifeEvent(kind="user", text="say hi", id="evt-approve")

    paused = asyncio.run(
        run_cognitive_cycle(
            event,
            LifeState(),
            provider=SpeakingProvider(),
            thread_id=thread_id,
            checkpointer=checkpointer,
            approvals=approvals,
        )
    )
    assert paused["awaiting_approval"] is True
    assert paused.get("dispatched") is not True
    audit_id = paused["approval_audit_id"]
    assert audit_id
    assert len(checkpointer.load(thread_id).outbox) == 0

    resumed = asyncio.run(
        resume_after_approval(thread_id, audit_id, "approve", checkpointer=checkpointer, approvals=approvals)
    )
    assert resumed["approval_status"] == "approved"
    assert resumed["dispatched"] is True
    assert len(checkpointer.load(thread_id).outbox) == 1


def test_interrupt_reject_skips_dispatch():
    checkpointer = MemoryCheckpointer()
    approvals = ApprovalRegistry()
    thread_id = "thread-reject"
    event = LifeEvent(kind="user", text="say hi", id="evt-reject")

    paused = asyncio.run(
        run_cognitive_cycle(
            event,
            LifeState(),
            provider=SpeakingProvider(),
            thread_id=thread_id,
            checkpointer=checkpointer,
            approvals=approvals,
        )
    )
    audit_id = paused["approval_audit_id"]
    resumed = asyncio.run(
        resume_after_approval(thread_id, audit_id, "reject", checkpointer=checkpointer, approvals=approvals)
    )
    assert resumed["approval_status"] == "rejected"
    assert resumed.get("dispatched") is not True
    assert len(checkpointer.load(thread_id).outbox) == 0


def test_rejected_gate_still_checkpoints_without_dispatch():
    checkpointer = MemoryCheckpointer()
    event = LifeEvent(kind="touch", id="evt-touch")
    first = asyncio.run(run_cognitive_cycle(event, LifeState(), thread_id="t-gate", checkpointer=checkpointer))
    second = asyncio.run(
        run_cognitive_cycle(event, first["life_state"], thread_id="t-gate", checkpointer=checkpointer)
    )
    assert second["accepted"] is False
    assert second.get("dispatched") is not True
    assert checkpointer.restore("t-gate") is not None


def test_graph_degraded_skips_dispatch():
    class BoomProvider:
        async def decide(self, event, state):
            from brain.models import ProviderError, ProviderErrorCategory

            raise ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message="down")

    checkpointer = MemoryCheckpointer()
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind="user", text="hello"),
            LifeState(),
            provider=BoomProvider(),
            checkpointer=checkpointer,
        )
    )
    assert result["degraded"] is True
    assert result.get("dispatched") is not True


def test_provider_failure_does_not_replay_checkpointed_intent():
    checkpointer = MemoryCheckpointer()
    thread_id = "thread-degraded"
    event = LifeEvent(kind="user", text="hello", id="evt-degraded")

    first = asyncio.run(run_cognitive_cycle(event, LifeState(), thread_id=thread_id, checkpointer=checkpointer))
    assert first["dispatched"] is True
    assert checkpointer.load(thread_id).outbox

    class FailingProvider:
        async def decide(self, event, state):
            from brain.models import ProviderError, ProviderErrorCategory
            raise ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message="offline")

    second = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind="user", text="hello again", id="evt-degraded-2"),
            first["life_state"],
            provider=FailingProvider(),
            thread_id=thread_id,
            checkpointer=checkpointer,
        )
    )
    assert second["degraded"] is True
    assert second["degrade_reason"] == "upstream_unavailable"
    assert second["execution_mode"] == "DEGRADED_LOCAL"
    assert second.get("intent") is None
    assert len(checkpointer.load(thread_id).outbox) == 1


def test_approval_checkpoint_contains_intent_and_restart_audit():
    checkpointer = MemoryCheckpointer()
    approvals = ApprovalRegistry()
    paused = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind="user", text="say hi", id="evt-restart"),
            LifeState(),
            provider=SpeakingProvider(),
            thread_id="thread-restart",
            checkpointer=checkpointer,
            approvals=approvals,
        )
    )
    audit_id = paused["approval_audit_id"]
    cp = checkpointer.restore("thread-restart")
    assert cp is not None and cp.pending_intent["name"] == "speaking"
    resumed = asyncio.run(
        resume_after_approval("thread-restart", audit_id, "restart", checkpointer=checkpointer, approvals=approvals)
    )
    assert resumed["approval_status"] == "restarted"
    assert resumed["audit_id"] == audit_id
    assert resumed["dispatched"] is False
    assert not checkpointer.load("thread-restart").outbox


def test_unregistered_tool_intent_is_rejected_without_side_effect():
    from brain.models import ToolIntent

    with pytest.raises(ValueError):
        validate_tool_intents([ToolIntent(name="shell")])
