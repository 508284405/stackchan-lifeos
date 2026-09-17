import asyncio

from brain.arbitration import arbitrate
from brain.flow import run_cognitive_cycle
from brain.models import BehaviorIntent, LifeEvent, LifeState, ProviderConfig
from brain.legacy_codex import CodexAppServerProvider
from brain.api import create_app
from brain.provider import Sub2APIProvider
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
        health = client.get("/health").json()
        response = client.post("/events", json={"kind": "user", "text": "hello"})
    assert response.status_code == 200
    assert response.json()["intent"]["name"] == "greet"
    assert health["thread_id"] != "default"
    assert health["checkpoint_mode"] == "memory"


def test_api_can_rehydrate_a_file_checkpoint(tmp_path):
    checkpoint_path = str(tmp_path / "brain-checkpoints.json")
    with TestClient(create_app(checkpoint_path=checkpoint_path, thread_id="api-thread")) as client:
        response = client.post("/events", json={"kind": "user", "text": "hello", "id": "api-event-1"})
        assert response.status_code == 200
    with TestClient(create_app(checkpoint_path=checkpoint_path, thread_id="api-thread")) as client:
        health = client.get("/health")
        assert health.status_code == 200
        response = client.post("/events", json={"kind": "system", "id": "api-event-2", "priority": 20})
        assert response.status_code == 200
        assert response.json()["state"]["interaction_count"] == 1


def test_api_uses_environment_thread_and_durable_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEOS_THREAD_ID", "host-thread-01")
    monkeypatch.setenv("LIFEOS_CHECKPOINT_PATH", str(tmp_path / "brain.json"))

    with TestClient(create_app()) as client:
        health = client.get("/health").json()

    assert health["thread_id"] == "host-thread-01"
    assert health["checkpoint_mode"] == "json_file"


def test_sub2api_health_is_checked_without_exposing_upstream_details():
    class Transport:
        def __init__(self):
            self.health_calls = 0

        async def health_check(self):
            self.health_calls += 1
            return {"data": [{"id": "test-model"}], "secret": "must-not-leak"}

        async def create_response(self, payload):
            return {"id": "r", "intents": [{"name": "idle", "priority": 1}]}

    transport = Transport()
    provider = Sub2APIProvider(
        ProviderConfig(
            base_url="http://127.0.0.1:8080",
            model="test-model",
            api_key="test-key",
        ),
        transport=transport,
    )

    with TestClient(create_app(provider=provider, thread_id="provider-thread")) as client:
        health = client.get("/health").json()

    assert transport.health_calls == 1
    assert health["provider_health"] == {
        "checked": True,
        "status": "healthy",
        "model_available": True,
    }
    assert "must-not-leak" not in str(health)
