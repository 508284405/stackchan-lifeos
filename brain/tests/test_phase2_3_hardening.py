"""Cross-cutting Phase 2/3 recovery, safety, and observability checks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from brain.checkpoint import Checkpoint, JsonFileCheckpointer, MemoryCheckpointer
from brain.dispatch import LocalToolExecutor
from brain.flow import replay_dispatch, resume_after_approval, run_cognitive_cycle
from brain.interrupt import ApprovalRegistry
from brain.metrics import MetricsRecorder
from brain.models import BehaviorIntent, EventKind, LifeEvent, LifeState, ToolIntent
from brain.preferences import UserPreferences
from brain.store import LifeOSStore
from brain.target import TargetSelector


class SpeakingProvider:
    async def decide(self, event, state):
        return [BehaviorIntent(name="speaking", reason="original speech", speech="original text", priority=50)]


def test_json_checkpoint_survives_a_new_store_instance(tmp_path):
    path = tmp_path / "checkpoints.json"
    first = JsonFileCheckpointer(path)
    first.save(Checkpoint(thread_id="thread-1", state={"safe": True}))
    second = JsonFileCheckpointer(path)
    restored = second.restore("thread-1")
    assert restored is not None and restored.state == {"safe": True}


def test_graph_restores_life_state_without_replaying_old_outbox(tmp_path):
    path = tmp_path / "checkpoints.json"
    checkpointer = JsonFileCheckpointer(path)
    first = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="hello", id="event-1"),
            thread_id="thread-persist",
            checkpointer=checkpointer,
        )
    )
    assert first["dispatched"] is True
    restarted = JsonFileCheckpointer(path)

    class FailingProvider:
        async def decide(self, event, state):
            from brain.models import ProviderError, ProviderErrorCategory

            raise ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message="offline")

    second = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="again", id="event-2"),
            provider=FailingProvider(),
            thread_id="thread-persist",
            checkpointer=restarted,
        )
    )
    assert second["checkpoint_recovery"] == "restored"
    assert second["degraded"] is True
    assert second["life_state"].interaction_count == first["life_state"].interaction_count + 1
    assert len(restarted.restore("thread-persist").outbox) == 1


def test_checkpoint_drops_untrusted_event_payloads(tmp_path):
    path = tmp_path / "safe.json"
    checkpointer = JsonFileCheckpointer(path)
    asyncio.run(
        run_cognitive_cycle(
            LifeEvent(
                kind=EventKind.USER,
                text="hello",
                value={"raw_media": "RAW_VALUE", "secret": "SECRET_VALUE"},
                extra_secret="EXTRA_SECRET",
            ),
            thread_id="thread-safe",
            checkpointer=checkpointer,
        )
    )
    encoded = path.read_text(encoding="utf-8")
    assert "RAW_VALUE" not in encoded
    assert "SECRET_VALUE" not in encoded
    assert "EXTRA_SECRET" not in encoded


def test_approval_can_resume_from_checkpoint_after_registry_restart():
    checkpointer = MemoryCheckpointer()
    original_registry = ApprovalRegistry()
    paused = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="say it", id="event-approval"),
            provider=SpeakingProvider(),
            thread_id="thread-approval-restart",
            checkpointer=checkpointer,
            approvals=original_registry,
        )
    )
    resumed = asyncio.run(
        resume_after_approval(
            "thread-approval-restart",
            paused["approval_audit_id"],
            "approve",
            checkpointer=checkpointer,
            approvals=ApprovalRegistry(),
        )
    )
    assert resumed["approval_status"] == "approved"
    assert resumed["intent"].speech == "original text"
    assert resumed["dispatched"] is True


def test_replay_dispatch_cannot_bypass_pending_approval():
    checkpointer = MemoryCheckpointer()
    approvals = ApprovalRegistry()
    paused = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="say it", id="event-pending"),
            provider=SpeakingProvider(),
            thread_id="thread-pending",
            checkpointer=checkpointer,
            approvals=approvals,
        )
    )
    replayed = asyncio.run(replay_dispatch("thread-pending", checkpointer=checkpointer))
    assert replayed["awaiting_approval"] is True
    assert replayed["dispatched"] is False
    assert not checkpointer.load("thread-pending").outbox


def test_target_unavailable_blocks_provider_target_search():
    class TargetProvider:
        async def decide(self, event, state):
            return [BehaviorIntent(name="look_at_person", priority=70)]

    selector = TargetSelector()
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(
                kind=EventKind.TIMER,
                id="event-target",
                timestamp=datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            ),
            LifeState(mood="curious", attention=0.5),
            provider=TargetProvider(),
            target_selector=selector,
        )
    )
    assert result["intent"].name != "look_at_person"


def test_metrics_summary_is_bounded_and_has_percentiles():
    metrics = MetricsRecorder()
    for index, latency in enumerate((10.0, 20.0, 30.0)):
        metrics.record(run_id="r", thread_id="t", event_id=str(latency), node="provider", latency_ms=latency, usage={"input_tokens": index + 1})
    metrics.record(run_id="r", thread_id="t", event_id="e", node="provider", provider_error_category="timeout")
    summary = metrics.summary(node="provider")
    assert summary["count"] == 4
    assert summary["latency_ms"]["p50"] == 20.0
    assert summary["latency_ms"]["p95"] == 30.0
    assert summary["provider_errors"] == {"timeout": 1}
    assert summary["usage"] == {"input_tokens": 6}


def test_phase3_policy_snapshots_keep_search_and_proactive_caps(tmp_path):
    from datetime import datetime, timezone
    from brain.proactive import ProactivePolicy

    moment = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    selector = TargetSelector()
    selector.update(True, moment)
    policy = ProactivePolicy(max_per_hour=1, cooldown_seconds=60)
    policy.record_proactive(moment)
    checkpoint = JsonFileCheckpointer(tmp_path / "phase3.json")
    checkpoint.save(
        Checkpoint(
            thread_id="phase3",
            state={"target_selector": selector.snapshot(), "proactive_policy": policy.snapshot()},
        )
    )
    restored = checkpoint.restore("phase3")
    assert TargetSelector.from_snapshot(restored.state["target_selector"]).phase.value == "tracked"
    restored_policy = ProactivePolicy.from_snapshot(restored.state["proactive_policy"])
    allowed, reason = restored_policy.can_propose(moment, UserPreferences())
    assert not allowed and reason == "hourly_cap"


def test_local_tool_executor_requires_explicit_allowlist_and_is_idempotent():
    calls = []
    executor = LocalToolExecutor({"memory.recall"})
    executor.register("memory.recall", lambda args: calls.append(args) or {"items": []}, allowed_args={"query"})
    intent = ToolIntent(name="memory.recall", args={"query": "hello"})
    first = executor.execute(intent, idempotency_key="tool-1")
    second = executor.execute(intent, idempotency_key="tool-1")
    assert first["executed"] is True and second["duplicate"] is True
    assert len(calls) == 1


def test_graph_executes_only_the_registered_read_only_tool():
    store = LifeOSStore()
    store.put(("lifeos", "memory"), "name", {"summary": "Stack"}, reason="seed", run_id="seed")

    class MemoryProvider:
        last_tool_intents = [ToolIntent(name="memory.recall", args={"query": "Stack"})]

        async def decide(self, event, state):
            return [BehaviorIntent(name="idle", priority=1)]

    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="recall", id="event-tool"),
            provider=MemoryProvider(),
            memory_store=store,
        )
    )
    assert result["degraded"] is False
    assert result["tool_results"][0]["result"]["memories"][0]["summary"] == "Stack"


def test_graph_rejects_unregistered_remote_tool_before_execution():
    class RemoteToolProvider:
        last_tool_intents = [ToolIntent(name="shell", args={"command": "rm -rf"})]

        async def decide(self, event, state):
            return [BehaviorIntent(name="idle", priority=1)]

    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="tool", id="event-bad-tool"),
            provider=RemoteToolProvider(),
        )
    )
    assert result["degraded"] is True
    assert "tool intent not registered" in result["degrade_reason"]
    assert not result.get("tool_results")
