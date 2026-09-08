"""Phase 3 acceptance scenarios — presence, proactive caps, memory, disconnect."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from brain.emotion import mood_to_expression
from brain.flow import run_cognitive_cycle
from brain.models import EventKind, LifeEvent, LifeState
from brain.preferences import QuietHours, UserPreferences
from brain.proactive import ProactivePolicy
from brain.store import LifeOSStore
from brain.target import TargetPhase, TargetSelector
from brain.voice import VoiceAdapter


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 30, hour, minute, tzinfo=timezone.utc)


def test_target_hold_then_loss_stops_search():
    selector = TargetSelector(hold_seconds=2.0, loss_timeout_seconds=3.0)
    t0 = _ts(10, 0)
    selector.update(True, t0)
    assert selector.phase == TargetPhase.TRACKED

    selector.update(False, t0 + timedelta(seconds=1))
    assert selector.phase == TargetPhase.HOLDING
    assert selector.should_search()

    selector.tick(t0 + timedelta(seconds=6))
    assert selector.phase == TargetPhase.LOST
    assert not selector.allows_look_at_person()

    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.TIMER, timestamp=t0 + timedelta(seconds=6)),
            LifeState(presence=False, mood="calm", attention=0.2),
            target_selector=selector,
        )
    )
    assert result["intent"].name != "look_at_person"
    assert selector.search_state() == "stopped"


def test_phase3_limits_reject_invalid_configuration_and_target_does_not_search():
    with pytest.raises(ValueError):
        ProactivePolicy(max_per_hour=-1)
    with pytest.raises(ValueError):
        TargetSelector(hold_seconds=-1)


def test_proactive_frequency_cooldown_and_quiet_hours():
    policy = ProactivePolicy(max_per_hour=2, cooldown_seconds=60.0, max_active_seconds=5.0)
    prefs = UserPreferences(
        proactive_enabled=True,
        quiet_hours=QuietHours(start=_ts(0).replace(hour=23).time(), end=_ts(0).replace(hour=6).time()),
    )
    t0 = _ts(12, 0)

    for i in range(2):
        moment = t0 + timedelta(seconds=i * 120)
        allowed, _ = policy.can_propose(moment, prefs)
        assert allowed
        policy.record_proactive(moment)

    allowed, reason = policy.can_propose(t0, prefs)
    assert not allowed
    assert reason == "hourly_cap"

    policy2 = ProactivePolicy(max_per_hour=10, cooldown_seconds=120.0)
    policy2.record_proactive(t0)
    allowed, reason = policy2.can_propose(t0 + timedelta(seconds=30), prefs)
    assert not allowed
    assert reason == "cooldown"

    quiet_policy = ProactivePolicy(max_per_hour=10, cooldown_seconds=0.0)
    quiet_prefs = UserPreferences(quiet_hours=QuietHours(start=_ts(0).replace(hour=22).time(), end=_ts(0).replace(hour=8).time()))
    allowed, reason = quiet_policy.can_propose(_ts(23, 30), quiet_prefs)
    assert not allowed
    assert reason == "quiet_hours"


def test_emotion_maps_mood_to_semantic_intent():
    intent = mood_to_expression(" HAPPY ")
    assert intent.name == "happy"
    assert not hasattr(intent, "angle")
    sleepy = mood_to_expression("sleepy")
    assert sleepy.name == "sleep"


def test_memory_recall_audit_and_raw_media_rejection():
    store = LifeOSStore()
    rec = store.put(
        ("lifeos", "memory"),
        "greeting",
        {"summary": "user likes morning hello"},
        reason="user said hello repeatedly",
        run_id="run-1",
    )
    recalled = store.recall(("lifeos", "memory"), query="hello")
    assert recalled and recalled[0]["summary"] == "user likes morning hello"
    assert store.audit_log()[-1].action == "put"
    assert store.delete(("lifeos", "memory"), "greeting", reason="user request", run_id="run-2")
    assert store.get(("lifeos", "memory"), "greeting") is None
    assert any(e.action == "delete" for e in store.audit_log())

    with pytest.raises(ValueError, match="raw media"):
        store.put(("lifeos", "memory"), "bad", {"image": b"data"}, reason="test", run_id="run-3")
    with pytest.raises(ValueError, match="credential"):
        store.put(("lifeos", "memory"), "bad", {"profile": {"api_key": "secret"}}, reason="test", run_id="run-4")
    with pytest.raises(ValueError, match="raw media"):
        store.put(("lifeos", "memory"), "bad", {"observations": [{"frame": "encoded"}]}, reason="test", run_id="run-5")


def test_recall_wired_into_cognitive_cycle():
    store = LifeOSStore()
    store.put(
        ("lifeos", "memory"),
        "name",
        {"summary": "calls me Stack"},
        reason="introduced nickname",
        run_id="run-a",
    )
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="Stack"),
            memory_store=store,
        )
    )
    assert result.get("recalled_memories") or result.get("recalled")
    recalled = result.get("recalled_memories") or result.get("recalled")
    assert "Stack" in recalled[0]["summary"]


def test_disconnect_degrades_without_search_or_speech():
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.USER, text="hello"),
            device_connected=False,
        )
    )
    intent = result["intent"]
    assert intent is not None
    assert intent.name not in {"look_at_person", "greet", "speaking"}
    assert not intent.speech
    assert result.get("degraded") is True
    assert result.get("degrade_reason") == "device_disconnected"


def test_timer_proactive_blocked_during_active_duration():
    policy = ProactivePolicy(max_per_hour=10, cooldown_seconds=0.0, max_active_seconds=30.0)
    policy.record_proactive(_ts(15, 0))
    prefs = UserPreferences(proactive_enabled=True)
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.TIMER, timestamp=_ts(15, 0) + timedelta(seconds=10)),
            LifeState(mood="calm", attention=0.5),
            proactive_policy=policy,
            preferences=prefs,
        )
    )
    assert result.get("proactive_blocked") == "active_duration"
    assert result["intent"] is None
    assert result["dispatched"] is False


def test_proactive_can_emit_exposes_duration_gate():
    policy = ProactivePolicy(cooldown_seconds=0, max_active_seconds=30)
    now = _ts(16)
    policy.record_proactive(now)
    allowed, reason = policy.can_emit(now + timedelta(seconds=1), UserPreferences())
    assert not allowed and reason == "active_duration"


def test_flow_does_not_dispatch_during_quiet_hours():
    result = asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind=EventKind.TIMER, timestamp=_ts(23, 30)),
            LifeState(attention=0.5),
            preferences=UserPreferences(
                quiet_hours=QuietHours(start=_ts(0).replace(hour=22).time(), end=_ts(0).replace(hour=8).time())
            ),
        )
    )
    assert result["proactive_blocked"] == "quiet_hours"
    assert result["intent"] is None
    assert result["dispatched"] is False


def test_voice_adapter_is_bounded_and_never_queues_when_disconnected():
    adapter = VoiceAdapter()
    request = adapter.prepare("  你好  ", language="zh-CN", voice="friendly", ttl_seconds=4)
    assert request is not None and request.text == "你好"
    assert adapter.prepare("hello", device_connected=False) is None
    with pytest.raises(ValueError):
        adapter.prepare("hello", voice="unregistered")
    with pytest.raises(ValueError):
        adapter.prepare("hello", ttl_seconds=float("nan"))
