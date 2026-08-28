"""Optional lightweight FastAPI/WebSocket adapter."""

import asyncio

from fastapi import FastAPI, WebSocket

from .flow import run_cognitive_cycle
from .models import LifeEvent, LifeState


def create_app() -> FastAPI:
    app = FastAPI(title="StackChan LifeOS Brain", version="0.1.0")
    state = LifeState()
    state_lock = None

    def get_state_lock():
        nonlocal state_lock
        if state_lock is None:
            state_lock = asyncio.Lock()
        return state_lock

    def response_payload(result):
        intent = result.get("intent")
        return {
            "accepted": result.get("accepted", False),
            "intent": intent.model_dump(mode="json") if intent else None,
            "state": result["life_state"].model_dump(mode="json"),
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "device_id": state.device_id}

    @app.post("/events")
    async def events(event: LifeEvent):
        nonlocal state
        async with get_state_lock():
            result = await run_cognitive_cycle(event, state)
            state = result["life_state"]
            return response_payload(result)

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        nonlocal state
        await websocket.accept()
        while True:
            event = LifeEvent.model_validate(await websocket.receive_json())
            async with get_state_lock():
                result = await run_cognitive_cycle(event, state)
                state = result["life_state"]
                payload = response_payload(result)
            await websocket.send_json(payload)

    return app
