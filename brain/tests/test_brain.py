import asyncio

from brain.arbitration import arbitrate
from brain.flow import run_cognitive_cycle
from brain.models import BehaviorIntent, LifeEvent, LifeState
from brain.provider import CodexAppServerProvider
from brain.api import create_app
from fastapi.testclient import TestClient


def test_user_event_updates_state_and_returns_semantic_intent():
    result = asyncio.run(run_cognitive_cycle(LifeEvent(kind="user", text="hello")))
    assert result["accepted"] is True
    assert result["life_state"].interaction_count == 1
    assert result["intent"].name == "greet"
    assert not hasattr(result["intent"], "servo_position")


def test_duplicate_event_is_filtered():
    event = LifeEvent(kind="touch")
    first = asyncio.run(run_cognitive_cycle(event, LifeState()))
    second = asyncio.run(run_cognitive_cycle(event, first["life_state"]))
    assert second["accepted"] is False
    assert second.get("intent") is None


def test_arbitration_prefers_highest_priority():
    chosen = arbitrate(
        [BehaviorIntent(name="idle", priority=1), BehaviorIntent(name="alert", priority=90)],
        LifeEvent(kind="system"),
    )
    assert chosen.name == "alert"


def test_codex_provider_initializes_before_turn():
    class Transport:
        def __init__(self):
            self.methods = []

        async def request(self, method, params):
            self.methods.append(method)
            if method == "initialize":
                return {}
            if method == "thread/start":
                return {"threadId": "test-thread"}
            raise AssertionError(f"unexpected request method: {method}")

        async def turn(self, *, thread_id, prompt, output_schema):
            self.methods.append("turn/start")
            assert thread_id == "test-thread"
            assert "life_state" in prompt
            assert output_schema["required"] == ["intents"]
            return {"intents": [{"name": "idle"}]}

    transport = Transport()
    provider = CodexAppServerProvider(transport)
    result = asyncio.run(provider.decide(LifeEvent(kind="timer"), LifeState()))
    assert result[0].name == "idle"
    assert transport.methods == ["initialize", "thread/start", "turn/start"]


def test_http_event_response_is_json_serializable():
    with TestClient(create_app()) as client:
        response = client.post("/events", json={"kind": "user", "text": "hello"})
    assert response.status_code == 200
    assert response.json()["intent"]["name"] == "greet"
