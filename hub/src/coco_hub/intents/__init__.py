"""Intent pipeline: normalise → rules engine → (fallback).

Each feature module exposes ``register(registry: IntentRegistry) -> None`` and is listed in
``INTENT_MODULES``; ``build_registry()`` runs them all at app startup.
"""

from collections.abc import Callable

from coco_hub.intents import builtin, timers, weather
from coco_hub.intents.engine import IntentRegistry

INTENT_MODULES: list[Callable[[IntentRegistry], None]] = [
    builtin.register,
    timers.register,
    weather.register,
]


def build_registry() -> IntentRegistry:
    registry = IntentRegistry()
    for register in INTENT_MODULES:
        register(registry)
    return registry
