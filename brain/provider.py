"""LLM boundary.

The real Codex app-server integration intentionally remains an adapter seam:
the brain can be tested and shipped without a logged-in Codex process.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
from typing import Any, Protocol

from .models import BehaviorIntent, LifeEvent, LifeState


class CodexProvider(ABC):
    @abstractmethod
    async def decide(self, event: LifeEvent, state: LifeState) -> list[BehaviorIntent]:
        """Return semantic intents only; providers must not touch hardware."""


class AppServerTransport(Protocol):
    """Persistent JSON-RPC transport that collects a full app-server turn.

    Implementations must consume notifications through ``turn/completed`` and
    surface approval requests to LangGraph; they must never auto-approve them.
    """

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class DeterministicCodexProvider(CodexProvider):
    """Offline provider used by default for development and deterministic tests."""

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


class CodexAppServerProvider(CodexProvider):
    """Protocol boundary for a future local ``codex app-server`` adapter.

    This class intentionally raises until a transport is supplied.  Keeping
    the boundary explicit avoids accidentally invoking a user's authenticated
    CLI from a sensor loop.
    """

    def __init__(self, transport: AppServerTransport | None = None):
        self.transport = transport
        self.initialized = False
        self.thread_id = None

    async def initialize(self) -> None:
        if self.transport is None:
            raise RuntimeError("Codex app-server transport is not configured")
        # JSON-RPC notifications (including unknown future notifications) are
        # consumed by the transport; only the initialize response is required.
        await self.transport.request(
            "initialize",
            {"clientInfo": {"name": "stackchan-lifeos", "version": "0.1.0"}},
        )
        self.initialized = True
        thread = await self.transport.request("thread/start", {"cwd": None})
        self.thread_id = thread.get("threadId") or thread.get("thread", {}).get("id")
        if not self.thread_id:
            raise RuntimeError("Codex app-server thread/start returned no thread id")

    async def decide(self, event: LifeEvent, state: LifeState) -> list[BehaviorIntent]:
        if self.transport is None:
            raise RuntimeError("Codex app-server transport is not configured")
        if not self.initialized:
            await self.initialize()
        prompt = json.dumps(
            {
                "event": event.model_dump(mode="json"),
                "life_state": state.model_dump(mode="json"),
                "instruction": "Return high-level behavior intents only; never hardware commands.",
            },
            ensure_ascii=False,
        )
        result = await self.transport.turn(
            thread_id=self.thread_id,
            prompt=prompt,
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["intents"],
                "properties": {
                    "intents": {
                        "type": "array",
                        "maxItems": 4,
                        "items": BehaviorIntent.model_json_schema(),
                    }
                },
            },
        )
        return [BehaviorIntent.model_validate(item) for item in result.get("intents", [])]
