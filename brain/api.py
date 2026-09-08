"""Optional lightweight FastAPI/WebSocket adapter."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, WebSocket

from .checkpoint import GRAPH_VERSION, PROVIDER_CONTRACT_VERSION, JsonFileCheckpointer, MemoryCheckpointer
from .config import provider_config_from_env
from .flow import run_cognitive_cycle
from .models import LifeEvent, LifeState
from .provider import DeterministicInferenceProvider, Sub2APIProvider


def _resolve_provider():
    kind = os.environ.get("STACKCHAN_COGNITION_PROVIDER", os.environ.get("SUB2API_PROVIDER", "fake")).lower()
    if kind in {"sub2api", "real"}:
        cfg = provider_config_from_env()
        if cfg is None:
            return DeterministicInferenceProvider(), {"mode": "disabled", "reason": "missing SUB2API configuration; deterministic local mode"}
        try:
            provider = Sub2APIProvider(cfg)
            return provider, {"mode": "sub2api", "model": cfg.model, "base_url": cfg.base_url}
        except Exception as exc:
            return DeterministicInferenceProvider(), {"mode": "disabled", "reason": f"provider config invalid: {exc}"}
    return DeterministicInferenceProvider(), {"mode": "fake"}


def create_app(
    provider=None,
    *,
    checkpointer: MemoryCheckpointer | None = None,
    checkpoint_path: str | None = None,
    thread_id: str = "default",
    store=None,
) -> FastAPI:
    app = FastAPI(title="StackChan LifeOS Brain", version="0.1.0")
    state: LifeState | None = None
    state_lock = None
    resolved_provider, provider_info = (provider, {"mode": "injected"}) if provider is not None else _resolve_provider()
    resolved_checkpointer = checkpointer
    if resolved_checkpointer is None and checkpoint_path:
        resolved_checkpointer = JsonFileCheckpointer(checkpoint_path)
    resolved_checkpointer = resolved_checkpointer or MemoryCheckpointer()

    def get_state_lock():
        nonlocal state_lock
        if state_lock is None:
            state_lock = asyncio.Lock()
        return state_lock

    def response_payload(result):
        intent = result.get("intent")
        degraded = bool(result.get("degraded"))
        return {
            "accepted": result.get("accepted", False),
            "intent": intent.model_dump(mode="json") if intent else None,
            "state": result["life_state"].model_dump(mode="json"),
            "degraded": degraded,
            "execution_mode": result.get("execution_mode", "ACTIVE" if not degraded else "DEGRADED_LOCAL"),
            "degrade_reason": result.get("degrade_reason") or (result.get("provider_error").category.value if result.get("provider_error") else None),
            "awaiting_approval": bool(result.get("awaiting_approval")),
            "approval_audit_id": result.get("approval_audit_id"),
        }

    @app.get("/health")
    async def health():
        current_state = state or LifeState()
        return {
            "ok": True,
            "device_id": current_state.device_id,
            "graph_version": GRAPH_VERSION,
            "provider_contract_version": PROVIDER_CONTRACT_VERSION,
            "provider": provider_info,
        }

    @app.post("/events")
    async def events(event: LifeEvent):
        nonlocal state
        async with get_state_lock():
            result = await run_cognitive_cycle(
                event,
                state,
                provider=resolved_provider,
                thread_id=thread_id,
                checkpointer=resolved_checkpointer,
                store=store,
            )
            state = result["life_state"]
            return response_payload(result)

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        nonlocal state
        await websocket.accept()
        while True:
            event = LifeEvent.model_validate(await websocket.receive_json())
            async with get_state_lock():
                result = await run_cognitive_cycle(
                    event,
                    state,
                    provider=resolved_provider,
                    thread_id=thread_id,
                    checkpointer=resolved_checkpointer,
                    store=store,
                )
                state = result["life_state"]
                payload = response_payload(result)
            await websocket.send_json(payload)

    return app
