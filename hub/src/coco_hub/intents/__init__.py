"""Intent pipeline: normalise → rules engine → (fallback).

Each feature module exposes ``register(registry: IntentRegistry) -> None`` and is listed in
``INTENT_MODULES``; ``build_registry()`` runs them all at app startup.

To let the Qwen fallback pick an intent, give it a ``description`` (one line for the prompt)
and, if its patterns capture slots, ``llm_slots`` (see ``engine.LlmSlot``;
``durations.llm_duration_slot()`` for durations).
"""

from collections.abc import Callable

from coco_hub.intents import builtin, media, pictures, sounds, timers, weather
from coco_hub.intents.engine import IntentRegistry

INTENT_MODULES: list[Callable[[IntentRegistry], None]] = [
    builtin.register,
    timers.register,
    weather.register,
    media.register,
    pictures.register,
    sounds.register,
]


def build_registry() -> IntentRegistry:
    registry = IntentRegistry()
    for register in INTENT_MODULES:
        register(registry)
    return registry
