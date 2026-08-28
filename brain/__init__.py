"""StackChan LifeOS cognitive brain.

The package deliberately keeps hardware concerns outside the brain.  It emits
safe, semantic behaviour intents which the firmware/actuator layer interprets.
"""

from .flow import build_cognitive_graph, run_cognitive_cycle
from .models import BehaviorIntent, LifeEvent, LifeState
from .provider import CodexProvider, DeterministicCodexProvider

__all__ = [
    "BehaviorIntent",
    "CodexProvider",
    "DeterministicCodexProvider",
    "LifeEvent",
    "LifeState",
    "build_cognitive_graph",
    "run_cognitive_cycle",
]
