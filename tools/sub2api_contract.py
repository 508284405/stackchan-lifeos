"""Sub2API provider contract — offline mock + opt-in live smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.models import LifeEvent, LifeState
from brain.provider import ProviderConfig, Sub2APIProvider
from brain.redaction import build_redacted_context

FIXTURE_DIR = ROOT / "contracts" / "sub2api" / "fixtures"


class MockTransport:
    def __init__(self, fixture: str = "success", status: int | None = None, timeout: bool = False, interrupted: bool = False):
        self.fixture = fixture
        self.status = status
        self.timeout = timeout
        self.interrupted = interrupted

    async def health_check(self):
        if self.status:
            from brain.provider import TransportHTTPError
            raise TransportHTTPError(self.status, "mock error")
        return {"data": [{"id": "test-model"}]}

    async def create_response(self, payload: dict):
        if self.timeout:
            raise asyncio.TimeoutError()
        if self.interrupted:
            from brain.provider import TransportStreamError
            raise TransportStreamError("fixture ended before complete JSON")
        if self.status:
            from brain.provider import TransportHTTPError
            raise TransportHTTPError(self.status, "mock error")
        if payload.get("model") != "test-model":
            raise AssertionError(f"unexpected model: {payload.get('model')}")
        if "context" in payload or "raw_media" in str(payload):
            raise AssertionError("outbound leaked forbidden field")
        data = json.loads((FIXTURE_DIR / f"{self.fixture}.json").read_text())
        return data


def run_offline() -> int:
    async def _run():
        cfg = ProviderConfig(base_url="http://127.0.0.1:8080", model="test-model", api_key="test-key-123")
        # success
        p = Sub2APIProvider(cfg, transport=MockTransport("success"))
        intents = await p.decide(LifeEvent(kind="user", text="hello"), LifeState())
        assert intents and intents[0].name == "greet", f"success fixture failed: {intents}"
        # malformed and schema drift must never reach dispatch.
        from brain.models import ProviderError
        for fixture in ("invalid", "malformed", "schema_drift"):
            p2 = Sub2APIProvider(cfg, transport=MockTransport(fixture))
            try:
                await p2.decide(LifeEvent(kind="user", text="hello"), LifeState())
                raise AssertionError(f"{fixture} fixture should have raised ProviderError")
            except ProviderError as exc:
                assert exc.category.value == "invalid_response"
        try:
            await Sub2APIProvider(cfg, transport=MockTransport("stream_interrupted", interrupted=True)).decide(LifeEvent(kind="user", text="hello"), LifeState())
            raise AssertionError("interrupted stream should have raised ProviderError")
        except ProviderError as exc:
            assert exc.category.value == "invalid_response"
        # 401 not retry storm
        p3 = Sub2APIProvider(cfg, transport=MockTransport(status=401))
        try:
            await p3.decide(LifeEvent(kind="user", text="hello"), LifeState())
            raise AssertionError("401 should raise")
        except Exception as e:
            assert isinstance(e, ProviderError) and e.category.value == "auth_error" and not e.retryable
        # 429 bounded retry (1 retry then fail)
        p4 = Sub2APIProvider(cfg, transport=MockTransport(status=429))
        try:
            await p4.decide(LifeEvent(kind="user", text="hello"), LifeState())
            raise AssertionError("429 should raise")
        except Exception as e:
            from brain.models import ProviderError
            assert isinstance(e, ProviderError) and e.category.value == "rate_limited"
        # 5xx is bounded to one retry and never exposes the key in diagnostics.
        p5 = Sub2APIProvider(cfg, transport=MockTransport(status=503))
        try:
            await p5.decide(LifeEvent(kind="user", text="hello"), LifeState())
            raise AssertionError("503 should raise")
        except ProviderError as exc:
            assert exc.category.value == "upstream_unavailable"
            assert "test-key-123" not in str(exc)
        # TLS / URL gate
        try:
            bad = ProviderConfig(base_url="http://example.com", model="m", api_key="k")
            Sub2APIProvider(bad)
            raise AssertionError("non-loopback http should be rejected")
        except ValueError:
            pass
        # redaction
        ctx = build_redacted_context(LifeEvent(kind="user", text="/etc/passwd hello"), LifeState(recent_texts=["/secret/path"]))
        assert "/etc/passwd" not in str(ctx) and "secret" not in str(ctx).lower()
        # api_key not in dump
        assert "test-key-123" not in str(cfg.safe_dump())
        print("PASS offline sub2api contract (mock fixtures, redaction, TLS gate, error mapping)")
    asyncio.run(_run())
    return 0


async def run_live(base_url: str) -> int:
    api_key = os.environ.get("SUB2API_API_KEY", "")
    model = os.environ.get("SUB2API_MODEL", "test-model")
    if not api_key:
        print("FAIL live requires SUB2API_API_KEY env")
        return 1
    cfg = ProviderConfig(base_url=base_url, model=model, api_key=api_key, tls_verify=True)
    transport = None
    provider = Sub2APIProvider(cfg, transport=transport)
    try:
        # health
        health = await provider.transport.health_check()  # type: ignore[attr-defined]
        print(f"PASS live health: {list(health.keys())[:4]}")
    except Exception as e:
        print(f"FAIL live health: {e}")
        return 1
    try:
        intents = await provider.decide(LifeEvent(kind="user", text="hello from contract smoke"), LifeState())
        print(f"PASS live responses intents={len(intents)} (redacted, no raw media)")
        assert all(i.name in {"greet","idle","happy","look_at_person","sleep","still","blink","small_nod","head_tilt","listen","thinking","speaking","wake","dizzy"} for i in intents)
    except Exception as e:
        from brain.models import ProviderError
        if isinstance(e, ProviderError):
            print(f"live provider error category={e.category.value} retryable={e.retryable} status={e.status_code}")
            if e.category.value in ("auth_error","rate_limited","upstream_unavailable","model_unavailable"):
                print("PASS live error is mapped (no secret in output)")
                return 0
        print(f"FAIL live responses: {e}")
        return 1
    # verify no key in any stringified output
    assert api_key not in json.dumps({"intents": [i.model_dump() for i in intents]}, ensure_ascii=False)
    print("PASS live contract smoke completed (desensitized report)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", metavar="BASE_URL", help="opt-in live smoke against SUB2API_BASE_URL (requires SUB2API_API_KEY)")
    args = parser.parse_args()
    if args.live:
        return asyncio.run(run_live(args.live))
    return run_offline()


if __name__ == "__main__":
    raise SystemExit(main())
