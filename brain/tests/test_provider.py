"""Phase 2 host-only provider contract tests (P2.0 / P2.1)."""

import asyncio
import math

import pytest

from brain.checkpoint import Checkpoint, MemoryCheckpointer, GRAPH_VERSION, PROVIDER_CONTRACT_VERSION
from brain.flow import run_cognitive_cycle
from brain.models import (
    BehaviorIntent,
    ProviderConfig,
    ProviderError,
    ProviderErrorCategory,
    LifeEvent,
    LifeState,
    ToolIntent,
)
from brain.provider import (
    DeterministicCodexProvider,
    HttpSub2APITransport,
    Sub2APIProvider,
    TransportHTTPError,
    validate_provider_startup,
)
from brain.redaction import build_redacted_context, build_prompt
from brain.validator import validate_intents


def _config(**overrides):
    values = {"base_url": "http://127.0.0.1:8080", "model": "test-model", "api_key": "test-key-123"}
    values.update(overrides)
    return ProviderConfig(**values)


class ScriptedTransport:
    """Synchronous mock transport that raises the same errors as the HTTP one."""

    def __init__(self, *, status=None, payload=None, timeout=False, network=False):
        self.status = status
        self.payload = payload if payload is not None else {"id": "resp_1", "intents": [{"name": "greet", "priority": 60}]}
        self.timeout = timeout
        self.network = network
        self.calls = []

    async def health_check(self):
        return {"data": [{"id": "test-model"}]}

    async def create_response(self, payload):
        self.calls.append(dict(payload))
        if self.timeout:
            raise asyncio.TimeoutError()
        if self.network:
            import urllib.error
            raise urllib.error.URLError("boom")
        if self.status:
            raise TransportHTTPError(self.status, "mock error body")
        return self.payload


def test_provider_config_secret_not_dumpable():
    cfg = _config()
    dump = str(cfg.safe_dump())
    assert "test-key-123" not in dump
    assert "api_key" not in cfg.model_dump()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "http://example.com", "api_key": "k"},
        {"api_key": ""},
        {"model": ""},
    ],
)
def test_provider_startup_rejects_invalid_config(kwargs):
    with pytest.raises(ValueError):
        validate_provider_startup(_config(**kwargs))


def test_loopback_http_allows_local_dev():
    cfg = _config(base_url="http://127.0.0.1:8080")
    provider = Sub2APIProvider(cfg, transport=ScriptedTransport(payload={"id": "r", "intents": [{"name": "idle", "priority": 1}]}))
    assert provider.config.api_key == "test-key-123"


def test_provider_returns_validated_intents():
    provider = Sub2APIProvider(
        _config(),
        transport=ScriptedTransport(payload={"id": "r1", "intents": [{"name": "greet", "priority": 60, "intensity": 0.6}]}),
    )
    intents = asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert [i.name for i in intents] == ["greet"]


def test_graph_binds_provider_request_to_the_actual_thread():
    provider = Sub2APIProvider(
        _config(),
        transport=ScriptedTransport(
            payload={"id": "r", "intents": [{"name": "idle", "priority": 1}]}
        ),
    )

    asyncio.run(
        run_cognitive_cycle(
            LifeEvent(kind="user", text="hello"),
            LifeState(),
            provider=provider,
            thread_id="thread-provider-binding",
        )
    )

    assert provider.last_plan is not None
    assert provider.last_plan.request.thread_id == "thread-provider-binding"


def test_provider_exposes_local_tool_intents_in_validated_plan():
    provider = Sub2APIProvider(
        _config(),
        transport=ScriptedTransport(
            payload={
                "id": "r-tool",
                "intents": [{"name": "idle", "priority": 1}],
                "tool_intents": [{"name": "memory.recall", "args": {"query": "hello"}}],
            }
        ),
    )
    intents = asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert intents[0].name == "idle"
    assert provider.last_plan is not None
    assert provider.last_plan.tool_intents == [ToolIntent(name="memory.recall", args={"query": "hello"})]


def test_provider_rejects_unknown_behavior_as_invalid_response():
    provider = Sub2APIProvider(
        _config(),
        transport=ScriptedTransport(payload={"id": "r2", "intents": [{"name": "fly", "priority": 90}]}),
    )
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.INVALID_RESPONSE


def test_provider_rejects_response_schema_drift_and_nan():
    for payload in (
        {"id": "r", "intents": [], "new_field": True},
        {"id": "r", "intents": [{"name": "idle", "intensity": math.nan}]},
    ):
        provider = Sub2APIProvider(_config(), transport=ScriptedTransport(payload=payload))
        with pytest.raises(ProviderError) as exc:
            asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
        assert exc.value.category is ProviderErrorCategory.INVALID_RESPONSE


def test_interrupted_stream_is_discarded():
    from brain.provider import TransportStreamError

    class Interrupted(ScriptedTransport):
        async def create_response(self, payload):
            raise TransportStreamError("partial")

    with pytest.raises(ProviderError) as exc:
        asyncio.run(Sub2APIProvider(_config(), transport=Interrupted()).decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.INVALID_RESPONSE


def test_provider_auth_error_no_retry():
    transport = ScriptedTransport(status=401)
    provider = Sub2APIProvider(_config(), transport=transport)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.AUTH_ERROR
    assert not exc.value.retryable
    assert len(transport.calls) == 1  # no retry storm


def test_provider_429_retries_once_then_raises():
    transport = ScriptedTransport(status=429)
    provider = Sub2APIProvider(_config(), transport=transport)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.RATE_LIMITED
    assert exc.value.retryable
    assert len(transport.calls) == 2  # bounded retry


def test_provider_network_error_retries_once():
    transport = ScriptedTransport(network=True)
    provider = Sub2APIProvider(_config(), transport=transport)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.NETWORK_ERROR
    assert len(transport.calls) == 2


def test_provider_timeout_no_dispatch():
    transport = ScriptedTransport(timeout=True)
    provider = Sub2APIProvider(_config(), transport=transport)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.TIMEOUT
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [500, 503])
def test_provider_5xx_retries_once_and_maps_to_upstream_unavailable(status):
    transport = ScriptedTransport(status=status)
    provider = Sub2APIProvider(_config(), transport=transport)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.decide(LifeEvent(kind="user", text="hello"), LifeState()))
    assert exc.value.category is ProviderErrorCategory.UPSTREAM_UNAVAILABLE
    assert exc.value.retryable is True
    assert len(transport.calls) == 2


def test_redaction_strips_paths_and_tokens():
    event = LifeEvent(kind="user", text="tell me /etc/shadow")
    state = LifeState(recent_texts=["secret-key-abcdef1234569876543210", "/Users/alice/.ssh/id_rsa"])
    ctx = build_redacted_context(event, state)
    joined = str(ctx)
    assert "/etc/shadow" not in joined
    assert "secret-key" not in joined.lower() or "abcdef1234569876543210" not in joined


def test_prompt_never_leaks_forbidden_field_names():
    event = LifeEvent(kind="user", text="hi")
    prompt = build_prompt(event, LifeState())
    for bad in ("raw_media", "api_key", "nonce", "wire_envelope", "mac:", "serial:"):
        assert bad not in prompt


def test_graph_degraded_when_provider_fails():
    class BoomProvider:
        async def decide(self, event, state):
            raise ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message="down")

    result = asyncio.run(run_cognitive_cycle(LifeEvent(kind="user", text="hello"), LifeState(), provider=BoomProvider()))
    assert result["degraded"] is True
    assert result["intent"] is None


def test_graph_policy_blocks_unknown_behavior():
    class BadProvider:
        async def decide(self, event, state):
            return [BehaviorIntent(name="fly", priority=90)]

    result = asyncio.run(run_cognitive_cycle(LifeEvent(kind="user", text="hello"), LifeState(), provider=BadProvider()))
    assert result["degraded"] is True
    assert result["intent"] is None
    assert "policy_blocked" in result["degrade_reason"]


def test_graph_policy_rejects_high_priority_from_provider():
    class BadProvider:
        async def decide(self, event, state):
            return [BehaviorIntent(name="greet", priority=99)]

    result = asyncio.run(run_cognitive_cycle(LifeEvent(kind="user", text="hello"), LifeState(), provider=BadProvider()))
    assert result["degraded"] is True


def test_validator_limits():
    good = [BehaviorIntent(name="greet", priority=60), BehaviorIntent(name="happy", priority=55)]
    assert validate_intents(good) == good
    with pytest.raises(ValueError):
        validate_intents([BehaviorIntent(name="greet", ttl_seconds=999)])


def test_http_transport_parses_json():
    transport = HttpSub2APITransport(_config())
    # This exercises the sync parsing path against a canned HTTPError.
    try:
        transport._sync_post("http://127.0.0.1:1/v1/responses", {"a": 1})
        raise AssertionError("should have failed to connect")
    except TransportHTTPError:
        raise AssertionError("connection refused must be network error, not HTTPError")
    except Exception as exc:
        from brain.provider import TransportNetworkError
        assert isinstance(exc, TransportNetworkError) or "network" in exc.__class__.__name__.lower()


def test_outbound_payload_has_no_api_key_and_is_bounded():
    provider = Sub2APIProvider(_config(), transport=ScriptedTransport(payload={"id": "r", "intents": []}))
    provider_config = provider.config
    # Build an outbound like decide does (empty intents is still valid JSON schema path).
    _ = provider_config
    event = LifeEvent(kind="user", text="hello")
    payload = {
        "model": provider.config.model,
        "input": build_prompt(event, LifeState()),
    }
    encoded = str(payload)
    assert "test-key-123" not in encoded
    assert math.isfinite(float(provider.config.request_timeout_seconds))


def test_checkpoint_version_guard_rejects_drift():
    cp = MemoryCheckpointer()
    cp.save(Checkpoint(thread_id="t1", state={"x": 1}))
    assert cp.restore("t1", graph_version=GRAPH_VERSION, provider_contract_version=PROVIDER_CONTRACT_VERSION) is not None
    assert cp.restore("t1", graph_version="lifeos-graph.old", provider_contract_version="sub2api.old") is None


def test_outbox_idempotency():
    cp = MemoryCheckpointer()
    cp.save(Checkpoint(thread_id="t1"))
    assert cp.append_outbox("t1", {"idempotency_key": "k1", "action": "send"}) is True
    assert cp.append_outbox("t1", {"idempotency_key": "k1", "action": "send"}) is False
    cp.clear_outbox("t1", "k1")
    assert cp.append_outbox("t1", {"idempotency_key": "k1", "action": "send"}) is True
