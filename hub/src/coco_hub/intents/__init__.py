"""Intent pipeline: normalise → rules engine → (fallback).

Each feature module exposes ``register(registry: IntentRegistry) -> None`` and is listed in
``INTENT_MODULES``; ``build_registry()`` runs them all at app startup.

To let the Qwen fallback pick an intent, give it a ``description`` (one line for the prompt)
and, if its patterns capture slots, ``llm_slots`` (see ``engine.LlmSlot``;
``durations.llm_duration_slot()`` for durations).
"""

from collections.abc import Callable
from typing import Any

from coco_hub.intents.engine import IntentRegistry


def _intent_modules() -> list[Callable[[IntentRegistry], None]]:
    # Imported here, not at module level: feature modules import their services (e.g.
    # coco_hub.timers.service), which import coco_hub.intents.normalize, which runs this
    # package's __init__ first. Eager imports made ``import coco_hub.timers.service`` fail
    # with a circular import unless something else had imported coco_hub.intents already.
    from coco_hub.intents import builtin, media, music, pictures, sounds, timers, weather

    return [
        builtin.register,
        timers.register,
        weather.register,
        media.register,
        pictures.register,
        sounds.register,
        music.register,
    ]


def __getattr__(name: str) -> Any:
    if name == "INTENT_MODULES":
        return _intent_modules()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registry() -> IntentRegistry:
    registry = IntentRegistry()
    for register in _intent_modules():
        register(registry)
    return registry
