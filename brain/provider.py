"""LLM boundary — neutral inference provider with Sub2API adapter."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import ssl
from contextvars import ContextVar
from abc import ABC, abstractmethod
from typing import Any, Protocol

from .models import (
    BehaviorIntent,
    LifeEvent,
    LifeState,
    IntentPlan,
    ProviderConfig,
    ProviderError,
    ProviderErrorCategory,
    ProviderRequest,
    ProviderResult,
    ToolIntent,
)
from .redaction import build_prompt, build_redacted_context, validate_outbound_payload
from .validator import validate_intent_plan

ALLOWED_BEHAVIORS = frozenset(
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


def _is_loopback_host(host: str) -> bool:
    h = (host or "").lower().strip("[]")
    return h in {"localhost", "127.0.0.1", "::1", "127.0.0.1:80", "127.0.0.1:443"} or h.startswith("127.")


def validate_provider_startup(config: ProviderConfig) -> None:
    parsed = urllib.parse.urlparse(config.base_url)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname or ""
    if scheme not in {"http", "https"}:
        raise ValueError("base_url must be http:// or https://")
    if not host:
        raise ValueError("base_url host is required")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain credentials, query, or fragment")
    if scheme == "http":
        if not _is_loopback_host(host):
            raise ValueError("non-loopback base_url must use https")
    if not config.tls_verify:
        raise ValueError("tls verification cannot be disabled")
    if not config.api_key.strip():
        raise ValueError("api_key is required")
    if not config.model.strip():
        raise ValueError("model is required")


class InferenceProvider(ABC):
    @abstractmethod
    async def decide(self, event: LifeEvent, state: LifeState) -> list[BehaviorIntent]: ...


CodexProvider = InferenceProvider


class Sub2APITransport(Protocol):
    async def health_check(self) -> dict[str, Any]: ...
    async def create_response(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpSub2APITransport:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _opener(self) -> urllib.request.OpenerDirector:
        # Provider traffic must not be redirected through ambient proxy settings.
        context = ssl.create_default_context()
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))

    async def health_check(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync_get, f"{self.config.base_url.rstrip('/')}/v1/models")

    async def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync_post, f"{self.config.base_url.rstrip('/')}/v1/responses", payload)

    def _sync_get(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener().open(req, timeout=self.config.connect_timeout_seconds + self.config.request_timeout_seconds) as resp:
                body = resp.read()
                return _decode_json(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise TransportHTTPError(exc.code, body) from exc
        except TransportMalformedResponse:
            raise
        except Exception as exc:
            raise TransportNetworkError(str(exc)) from exc

    def _sync_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with self._opener().open(req, timeout=self.config.connect_timeout_seconds + self.config.request_timeout_seconds) as resp:
                body = resp.read()
                return _decode_json(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise TransportHTTPError(exc.code, body) from exc
        except TransportMalformedResponse:
            raise
        except Exception as exc:
            raise TransportNetworkError(str(exc)) from exc


class TransportHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"http {status}")
        self.status = status
        self.body = body


class TransportNetworkError(Exception):
    pass


class TransportStreamError(Exception):
    """A streamed response ended before a complete structured result arrived."""


class TransportMalformedResponse(Exception):
    """The upstream returned a non-JSON response."""


def _decode_json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportMalformedResponse("upstream returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise TransportMalformedResponse("upstream JSON response must be an object")
    return value


def _map_http_error(exc: TransportHTTPError) -> ProviderError:
    status = exc.status
    if status in (401, 403):
        return ProviderError(category=ProviderErrorCategory.AUTH_ERROR, message=f"auth failed: {status}", retryable=False, status_code=status)
    if status == 429:
        return ProviderError(category=ProviderErrorCategory.RATE_LIMITED, message="rate limited", retryable=True, status_code=status)
    if status in (404,):
        return ProviderError(category=ProviderErrorCategory.MODEL_UNAVAILABLE, message=f"model unavailable: {status}", retryable=False, status_code=status)
    if 500 <= status <= 599:
        return ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message=f"upstream {status}", retryable=True, status_code=status)
    return ProviderError(category=ProviderErrorCategory.UPSTREAM_UNAVAILABLE, message=f"http {status}", retryable=False, status_code=status)


def _validate_intents(raw: Any) -> list[BehaviorIntent]:
    if not isinstance(raw, list):
        raise ValueError("intents must be a list")
    if len(raw) > 4:
        raise ValueError("at most 4 intents")
    out: list[BehaviorIntent] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("intent must be object")
        if any(k not in {"name", "reason", "priority", "intensity", "speech", "ttl_seconds", "interruptible"} for k in item):
            raise ValueError(f"unknown intent field: {set(item) - {'name','reason','priority','intensity','speech','ttl_seconds','interruptible'}}")
        name = item.get("name")
        if name not in ALLOWED_BEHAVIORS:
            raise ValueError(f"behavior not registered: {name}")
        validated = BehaviorIntent.model_validate(item)
        if not _finite(validated.intensity) or not _finite(float(validated.priority)):
            raise ValueError("intent contains non-finite number")
        out.append(validated)
    return out


RESPONSE_FIELDS = frozenset({"id", "response_id", "model", "output", "intents", "tool_intents", "usage"})


def _extract_plan_parts(raw: Any) -> tuple[list[BehaviorIntent], list[ToolIntent]]:
    if not isinstance(raw, dict):
        raise ValueError("provider response must be an object")
    unknown = set(raw) - RESPONSE_FIELDS
    if unknown:
        raise ValueError(f"unknown response fields: {sorted(unknown)}")
    _reject_nonfinite(raw)
    intents_raw = raw.get("intents")
    tools_raw = raw.get("tool_intents")
    if intents_raw is None:
        output = raw.get("output")
        if isinstance(output, dict) and set(output) - {"intents", "tool_intents"}:
            raise ValueError("unknown output fields")
        if isinstance(output, dict):
            intents_raw = output.get("intents")
            tools_raw = output.get("tool_intents")
        elif isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or set(item) - {"type", "content"}:
                    raise ValueError("malformed output item")
                content = item.get("content", [])
                if not isinstance(content, list):
                    raise ValueError("malformed output content")
                for part in content:
                    if not isinstance(part, dict) or set(part) - {"type", "json"}:
                        raise ValueError("malformed output content part")
                    document = part.get("json")
                    if isinstance(document, dict):
                        if set(document) - {"intents", "tool_intents"}:
                            raise ValueError("unknown structured output fields")
                        intents_raw = document.get("intents")
                        tools_raw = document.get("tool_intents")
    if intents_raw is None:
        raise ValueError("missing intents in provider response")
    intents = _validate_intents(intents_raw)
    if tools_raw is None:
        tools_raw = []
    if not isinstance(tools_raw, list) or len(tools_raw) > 4:
        raise ValueError("tool_intents must be a list with at most 4 items")
    tool_intents = [ToolIntent.model_validate(item) for item in tools_raw]
    return intents, tool_intents


def _extract_intents(raw: Any) -> list[BehaviorIntent]:
    return _extract_plan_parts(raw)[0]


def _reject_nonfinite(value: Any) -> None:
    import math
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("response contains non-finite number")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child)


def _finite(v: float) -> bool:
    import math
    return math.isfinite(v)


class DeterministicInferenceProvider(InferenceProvider):
    async def decide(self, event: LifeEvent, state: LifeState) -> list[BehaviorIntent]:
        if event.kind == "user":
            text = event.text or ""
            speech = "我听到了。"
            if any(word in text.lower() for word in ("你好", "hello", "hi")):
                speech = "你好呀，很高兴见到你。"
            return [BehaviorIntent(name="greet", reason="user interaction", priority=60, speech=speech)]
        if event.kind == "touch":
            return [BehaviorIntent(name="happy", reason="top touch", priority=55)]
        if event.kind == "presence" and bool(event.value):
            return [BehaviorIntent(name="look_at_person", reason="presence detected", priority=40)]
        if event.kind == "motion":
            return [BehaviorIntent(name="dizzy", reason="motion detected", priority=30, intensity=0.4)]
        if event.kind == "timer" and state.energy < 0.2:
            return [BehaviorIntent(name="sleep", reason="low energy", priority=20)]
        return [BehaviorIntent(name="idle", reason="no salient action", priority=1, intensity=0.2)]


# Compatibility alias for the Phase 1 name; new code must use the neutral name.
DeterministicCodexProvider = DeterministicInferenceProvider


class Sub2APIProvider(InferenceProvider):
    def __init__(self, config: ProviderConfig, transport: Sub2APITransport | None = None):
        validate_provider_startup(config)
        self.config = config
        self.transport: Sub2APITransport = transport or HttpSub2APITransport(config)
        self.last_result: ProviderResult | None = None
        self.last_plan: IntentPlan | None = None
        self.last_tool_intents: list[ToolIntent] = []
        self._thread_id: ContextVar[str] = ContextVar(
            f"sub2api_thread_id_{id(self)}", default="provider"
        )

    def bind_thread(self, thread_id: str) -> None:
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 96:
            raise ValueError("provider thread_id must be a bounded non-empty string")
        self._thread_id.set(thread_id)

    async def decide(self, event: LifeEvent, state: LifeState) -> list[BehaviorIntent]:
        self.last_tool_intents = []
        self.last_plan = None
        self.last_result = None
        prompt = build_prompt(event, state)
        plan_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["intents"],
            "properties": {
                "intents": {
                    "type": "array",
                    "maxItems": 4,
                    "items": BehaviorIntent.model_json_schema(),
                },
                "tool_intents": {
                    "type": "array",
                    "maxItems": 4,
                    "items": ToolIntent.model_json_schema(),
                },
            },
        }
        outbound = {
            "model": self.config.model,
            "input": prompt,
            "stream": False,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": "intent_plan", "schema": plan_schema}},
        }
        validate_outbound_payload(outbound)
        start = time.monotonic()
        raw: dict[str, Any] | None = None
        last_error: ProviderError | None = None
        deadline = start + self.config.request_timeout_seconds * 2 + 0.5
        for attempt in range(2):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                raw = await asyncio.wait_for(self.transport.create_response(outbound), timeout=min(self.config.request_timeout_seconds, remaining))
                break
            except asyncio.TimeoutError as exc:
                last_error = ProviderError(category=ProviderErrorCategory.TIMEOUT, message="provider timeout", retryable=True)
                raise last_error from exc
            except TransportHTTPError as exc:
                mapped = _map_http_error(exc)
                last_error = mapped
                if mapped.retryable and attempt == 0:
                    await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                    continue
                raise mapped from exc
            except (TransportNetworkError, urllib.error.URLError, TimeoutError) as exc:
                last_error = ProviderError(category=ProviderErrorCategory.NETWORK_ERROR, message=str(exc)[:200], retryable=True)
                if attempt == 0:
                    await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                    continue
                raise last_error from exc
            except TransportStreamError as exc:
                raise ProviderError(category=ProviderErrorCategory.INVALID_RESPONSE, message="stream interrupted", retryable=False) from exc
            except TransportMalformedResponse as exc:
                raise ProviderError(category=ProviderErrorCategory.INVALID_RESPONSE, message="malformed provider response", retryable=False) from exc
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(category=ProviderErrorCategory.INVALID_RESPONSE, message=str(exc)[:200], retryable=False) from exc
        if raw is None:
            assert last_error is not None
            raise last_error
        latency_ms = (time.monotonic() - start) * 1000
        try:
            intents, tool_intents = _extract_plan_parts(raw)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(category=ProviderErrorCategory.INVALID_RESPONSE, message=str(exc)[:200], retryable=False) from exc
        usage: dict[str, int] = {}
        raw_usage = raw.get("usage")
        if isinstance(raw_usage, dict):
            for key, value in raw_usage.items():
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key[:32]] = value
        # Never retain upstream response bodies: they are untrusted and may contain secrets.
        self.last_result = ProviderResult(
            response_id=str(raw.get("id") or raw.get("response_id") or event.id),
            latency_ms=latency_ms,
            usage=usage,
        )
        try:
            self.last_plan = validate_intent_plan(
                IntentPlan(
                    request=ProviderRequest(
                        run_id=event.id,
                        thread_id=self._thread_id.get(),
                        event_id=event.id,
                        device_id=state.device_id,
                        event_kind=str(event.kind.value if hasattr(event.kind, "value") else event.kind),
                        context=build_redacted_context(event, state),
                        prompt=prompt,
                    ),
                    intents=intents,
                    tool_intents=tool_intents,
                    result=self.last_result,
                )
            )
        except Exception as exc:
            raise ProviderError(category=ProviderErrorCategory.INVALID_RESPONSE, message=str(exc)[:200], retryable=False) from exc
        self.last_tool_intents = list(tool_intents)
        return intents
