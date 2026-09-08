"""Phase 0 compatibility adapter, isolated from the Stage 2 runtime.

The production graph never imports this module. It is retained only so the
historical CLI contract can be replayed without making Sub2API or the graph
depend on a local Codex app-server process.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .models import BehaviorIntent, LifeEvent, LifeState
from .provider import InferenceProvider


class AppServerTransport(Protocol):
    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def turn(self, *, thread_id: str, prompt: str, output_schema: dict[str, Any]) -> dict[str, Any]: ...


class CodexAppServerProvider(InferenceProvider):
    """Historical adapter; never selected by Stage 2 configuration."""

    def __init__(self, transport: AppServerTransport | None = None):
        self.transport = transport
        self.initialized = False
        self.thread_id: str | None = None

    async def initialize(self) -> None:
        if self.transport is None:
            raise RuntimeError("Codex app-server transport is not configured")
        await self.transport.request("initialize", {"clientInfo": {"name": "stackchan-lifeos", "version": "0.1.0"}})
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
                "properties": {"intents": {"type": "array", "maxItems": 4, "items": BehaviorIntent.model_json_schema()}},
            },
        )
        return [BehaviorIntent.model_validate(item) for item in result.get("intents", [])]

