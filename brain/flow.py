"""LangGraph cognitive cycle: gate -> state update -> decide -> arbitrate."""

from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from .arbitration import arbitrate
from .gate import semantic_gate
from .models import BehaviorIntent, LifeEvent, LifeState, utc_now
from .provider import CodexProvider, DeterministicCodexProvider


class CognitiveGraphState(TypedDict, total=False):
    life_state: LifeState
    event: LifeEvent
    accepted: bool
    candidates: list[BehaviorIntent]
    intent: Optional[BehaviorIntent]


def build_cognitive_graph(provider: Optional[CodexProvider] = None):
    provider = provider or DeterministicCodexProvider()

    async def gate_node(data: CognitiveGraphState):
        state, event = data["life_state"], data["event"]
        return {"accepted": semantic_gate(event, state)}

    def route(data: CognitiveGraphState) -> str:
        return "update" if data.get("accepted") else "finish"

    def update_node(data: CognitiveGraphState):
        return {"life_state": data["life_state"].apply(data["event"])}

    async def decide_node(data: CognitiveGraphState):
        return {"candidates": await provider.decide(data["event"], data["life_state"])}

    def arbitrate_node(data: CognitiveGraphState):
        chosen = arbitrate(data.get("candidates", []), data["event"])
        state = data["life_state"]
        if chosen:
            state = state.model_copy(update={"active_behavior": chosen, "last_response_at": utc_now()})
        return {"intent": chosen, "life_state": state}

    graph = StateGraph(CognitiveGraphState)
    graph.add_node("gate", gate_node)
    graph.add_node("update", update_node)
    graph.add_node("decide", decide_node)
    graph.add_node("arbitrate", arbitrate_node)
    graph.set_entry_point("gate")
    graph.add_conditional_edges("gate", route, {"update": "update", "finish": END})
    graph.add_edge("update", "decide")
    graph.add_edge("decide", "arbitrate")
    graph.add_edge("arbitrate", END)
    return graph.compile()


async def run_cognitive_cycle(
    event: LifeEvent,
    life_state: Optional[LifeState] = None,
    provider: Optional[CodexProvider] = None,
) -> CognitiveGraphState:
    graph = build_cognitive_graph(provider)
    return await graph.ainvoke({"event": event, "life_state": life_state or LifeState()})
