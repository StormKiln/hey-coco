import json

import pytest

from coco_hub.intents import build_registry
from coco_hub.intents.durations import llm_duration_slot
from coco_hub.intents.engine import Intent, IntentRegistry, LlmSlot, Reply
from coco_hub.qwen.catalog import Catalog, Interpretation, InvalidOutput


def noop(match, request, ctx):
    return Reply(say="ok")


def demo_registry() -> IntentRegistry:
    r = IntentRegistry()
    r.register(
        Intent(
            "timer.create",
            patterns=[r"set a timer for (?P<duration>.+)"],
            handler=noop,
            description='start a countdown, including "remind me in X to Y"',
            llm_slots={
                "name": LlmSlot(hint='timer name is the food or thing being timed ("eggs").'),
                "duration_s": llm_duration_slot(),
            },
        )
    )
    r.register(
        Intent(
            "timer.cancel",
            patterns=[r"cancel (?P<name>\w+) timer"],
            handler=noop,
            description="stop or delete a running timer",
            llm_slots={"name": LlmSlot(), "all": LlmSlot(type="boolean")},
        )
    )
    r.register(
        Intent(
            "weather.forecast",
            patterns=[r"weather (?P<when>\w+)"],
            handler=noop,
            description="what the weather will be like later",
            llm_slots={"when": LlmSlot(enum=("today", "tomorrow"), required=True)},
        )
    )
    r.register(Intent("time.now", patterns=["time"], handler=noop, description="the time"))
    # Not offered: no description, or slots without llm_slots.
    r.register(Intent("debug.secret", patterns=["xyzzy"], handler=noop))
    r.register(
        Intent("legacy.slotted", patterns=[r"legacy (?P<x>\w+)"], handler=noop, description="x")
    )
    return r


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.from_registry(demo_registry())


def test_closed_set_comes_from_the_registry(catalog):
    assert catalog.intent_names == (
        "timer.create",
        "timer.cancel",
        "weather.forecast",
        "time.now",
        "general.answer",
        "none",
    )
    assert catalog.schema["properties"]["intent"]["enum"] == list(catalog.intent_names)


def test_schema_slots_are_the_union_of_llm_slots(catalog):
    schema = catalog.schema
    assert schema["required"] == ["intent", "slots", "answer"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["answer"] == {"type": ["string", "null"]}
    slots = schema["properties"]["slots"]
    assert slots["additionalProperties"] is False
    assert slots["properties"] == {
        "name": {"type": "string"},
        "duration_s": {"type": "integer"},
        "all": {"type": "boolean"},
        "when": {"type": "string", "enum": ["today", "tomorrow"]},
    }
    json.dumps(schema)  # sendable as Ollama's `format`


def test_system_prompt_follows_the_spike_v2_wording(catalog):
    prompt = catalog.system_prompt
    assert prompt.startswith("You are Coco, a voice assistant on a family kitchen iPad.")
    assert "Intents (slots in parentheses, ? = optional):" in prompt
    assert (
        '- timer.create (name?, duration_s) - start a countdown, including "remind me in X to Y".'
        in prompt
    )
    assert "- timer.cancel (name?, all?) - stop or delete a running timer." in prompt
    assert "- weather.forecast (when) - what the weather will be like later." in prompt
    assert "- time.now - the time." in prompt
    assert "- general.answer - any question about the world you can answer:" in prompt
    assert "- none - actions you cannot do:" in prompt
    assert 'duration_s is in SECONDS: "45 seconds" = 45' in prompt
    assert prompt.count("timer name is the food") == 1
    assert '"answer" is null for every intent except general.answer.' in prompt
    assert "debug.secret" not in prompt and "legacy.slotted" not in prompt


def test_new_intents_appear_without_code_changes():
    registry = demo_registry()
    registry.register(
        Intent("media.pause", patterns=["pause"], handler=noop, description="pause it")
    )
    assert "media.pause" in Catalog.from_registry(registry).intent_names


def test_real_registry_offers_builtins():
    names = Catalog.from_registry(build_registry()).intent_names
    assert {"time.now", "date.today", "spell.word", "general.answer", "none"} <= set(names)


def test_conflicting_slot_types_are_rejected():
    registry = demo_registry()
    registry.register(
        Intent(
            "volume.set",
            patterns=[r"volume (?P<name>\d+)"],
            handler=noop,
            description="set volume",
            llm_slots={"name": LlmSlot(type="integer")},
        )
    )
    with pytest.raises(ValueError, match="name"):
        Catalog.from_registry(registry)


def test_reserved_names_are_rejected():
    registry = IntentRegistry()
    registry.register(Intent("none", patterns=["x"], handler=noop, description="x"))
    with pytest.raises(ValueError, match="reserved"):
        Catalog.from_registry(registry)


def reply(intent, slots=None, answer=None) -> str:
    return json.dumps({"intent": intent, "slots": slots or {}, "answer": answer})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            reply("timer.create", {"name": "eggs", "duration_s": 600}),
            Interpretation("timer.create", {"name": "eggs", "duration_s": 600}),
        ),
        # repairs: null slots dropped, numeric strings coerced, foreign slots dropped
        (
            reply("timer.create", {"name": None, "duration_s": "660"}),
            Interpretation("timer.create", {"duration_s": 660}),
        ),
        (
            reply("timer.create", {"duration_s": 90.0, "query": "jazz"}),
            Interpretation("timer.create", {"duration_s": 90}),
        ),
        (
            reply("timer.cancel", {"all": "true"}),
            Interpretation("timer.cancel", {"all": True}),
        ),
        (
            reply("weather.forecast", {"when": "Tomorrow"}),
            Interpretation("weather.forecast", {"when": "tomorrow"}),
        ),
        # JSON wrapped in chatter (prompt-only models)
        (
            'Sure! {"intent": "time.now", "slots": {}, "answer": null} Hope that helps.',
            Interpretation("time.now"),
        ),
        # answer is ignored unless the intent is general.answer
        (reply("time.now", {}, "It is noon."), Interpretation("time.now")),
        (reply("none", {}, "I can't make phone calls."), Interpretation("none")),
        (
            reply("general.answer", {}, "  Canberra is the capital.  "),
            Interpretation("general.answer", answer="Canberra is the capital."),
        ),
    ],
)
def test_validate_accepts_and_repairs(catalog, raw, expected):
    assert catalog.validate(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json at all",
        '{"intent": "timer.create", "slots": {"duration_s": 6',
        "[1, 2]",
        reply("pizza.order"),
        reply("debug.secret"),  # registered, but not offered to the LLM
        reply("timer.create", {"name": "eggs"}),  # required duration missing
        reply("timer.create", {"duration_s": 0}),
        reply("timer.create", {"duration_s": 90_000}),  # over 24 h
        reply("timer.create", {"duration_s": "ten minutes"}),
        reply("timer.create", {"duration_s": True}),
        reply("weather.forecast", {"when": "next_year"}),
        reply("timer.cancel", {"all": "maybe"}),
        reply("timer.cancel", {"name": ""}),
        reply("general.answer", {}, None),
        reply("general.answer", {}, "   "),
        '{"intent": "timer.cancel", "slots": "eggs", "answer": null}',
    ],
)
def test_validate_rejects(catalog, raw):
    with pytest.raises(InvalidOutput):
        catalog.validate(raw)
