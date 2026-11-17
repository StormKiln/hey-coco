"""Which real intents Qwen can pick, and that its typed slots become slots the handlers read."""

from datetime import UTC, datetime

import pytest

from coco_hub.intents import build_registry
from coco_hub.intents.durations import parse_duration
from coco_hub.intents.weather import resolve_period
from coco_hub.qwen.catalog import Catalog

# Explicit on purpose: a new intent in these modules that forgets ``llm_slots`` (or a
# description) silently drops out of the fallback, and this list catches it.
QWEN_VISIBLE = {
    "time.now",
    "date.today",
    "spell.word",
    "timer.create",
    "timer.query",
    "timer.list",
    "timer.cancel",
    "timer.add_time",
    "alarm.stop",
    "weather.current",
    "weather.forecast",
    "weather.rain",
    "weather.high_low",
    "picture.show",
    "picture.next",
    "sound.play",
    "sound.again",
    "calendar.day",
    "calendar.find",
}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.from_registry(build_registry())


def test_timers_weather_pictures_sounds_are_visible_to_qwen(catalog):
    missing = QWEN_VISIBLE - set(catalog.intent_names)
    assert not missing, f"not offered to Qwen: {sorted(missing)}"


def slot(catalog: Catalog, intent: str, key: str, value) -> tuple[str, str]:
    return catalog.intents[intent].llm_slots[key].handler_slot(key, value)


@pytest.mark.parametrize("intent", ["timer.create", "timer.add_time"])
def test_timer_durations_are_seconds(catalog, intent):
    name, text = slot(catalog, intent, "duration_s", 660)
    assert name == "duration" and parse_duration(text) == 660
    assert catalog.intents[intent].llm_slots["duration_s"].required


@pytest.mark.parametrize(
    "intent", ["timer.create", "timer.query", "timer.cancel", "timer.add_time"]
)
def test_timer_name_is_optional(catalog, intent):
    assert slot(catalog, intent, "name", "eggs") == ("name", "eggs")
    assert not catalog.intents[intent].llm_slots["name"].required


@pytest.mark.parametrize(
    "junk", ["one", "it", "timer", "the timer", "countdown", "eight minutes", "45 seconds"]
)
def test_made_up_timer_names_are_dropped(catalog, junk):
    # The spike's known failure: qwen3:8b names timers "one", "countdown", or the duration.
    for intent in ("timer.create", "timer.query", "timer.cancel", "timer.add_time"):
        assert slot(catalog, intent, "name", junk) == ("name", "")


def test_real_timer_names_are_kept(catalog):
    assert slot(catalog, "timer.create", "name", "Eggs") == ("name", "Eggs")
    assert slot(catalog, "timer.create", "name", "the pizza") == ("name", "the pizza")


def test_timer_cancel_all(catalog):
    assert slot(catalog, "timer.cancel", "all", True) == ("all", "true")


def test_timer_name_hint_bans_filler_names(catalog):
    prompt = catalog.system_prompt
    assert "timer name is the food or thing being timed" in prompt
    for word in ('"timer"', '"countdown"', '"one"', '"it"'):
        assert word in prompt


@pytest.mark.parametrize("intent", ["weather.forecast", "weather.rain", "weather.high_low"])
def test_every_weather_when_is_a_period_the_handlers_accept(catalog, intent):
    spec = catalog.intents[intent].llm_slots["when"]
    assert spec.enum is not None and not spec.required
    now = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    for value in spec.enum:
        key, text = spec.handler_slot("when", value)
        assert key == "when"
        resolve_period(text, now)  # raises on anything the handlers can't read


def test_weather_when_reads_naturally(catalog):
    assert slot(catalog, "weather.forecast", "when", "this_weekend") == ("when", "this weekend")
    assert slot(catalog, "weather.rain", "when", "tomorrow_morning") == (
        "when",
        "tomorrow morning",
    )


@pytest.mark.parametrize(
    ("value", "handler"), [("high", "high"), ("low", "low"), ("both", "high and low")]
)
def test_weather_which(catalog, value, handler):
    assert slot(catalog, "weather.high_low", "which", value) == ("which", handler)


def test_picture_query_and_sound_name_are_required(catalog):
    assert catalog.intents["picture.show"].llm_slots["query"].required
    assert slot(catalog, "picture.show", "query", "red panda") == ("query", "red panda")
    assert catalog.intents["sound.play"].llm_slots["name"].required
    assert slot(catalog, "sound.play", "name", "fire truck") == ("name", "fire truck")
    assert "sound name is the plain animal or vehicle" in catalog.system_prompt


def test_schema_is_consistent(catalog):
    slots = catalog.schema["properties"]["slots"]["properties"]
    assert slots["duration_s"] == {"type": "integer"}
    assert slots["all"] == {"type": "boolean"}
    assert "this_weekend" in slots["when"]["enum"]
    assert slots["which"]["enum"] == ["high", "low", "both"]


@pytest.mark.parametrize(
    "intent", ["weather.current", "weather.forecast", "weather.rain", "weather.high_low"]
)
def test_weather_place_is_an_optional_string(catalog, intent):
    # #93: "what's the forecast today in Portland" lost the place to the home forecast.
    spec = catalog.intents[intent].llm_slots["place"]
    assert spec.type == "string" and not spec.required
    assert slot(catalog, intent, "place", "Portland, Oregon") == ("place", "Portland, Oregon")
    assert "place is a city or town the person named" in catalog.system_prompt
