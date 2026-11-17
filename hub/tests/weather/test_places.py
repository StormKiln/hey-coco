"""Weather for a place the person names (#93): "what's the forecast today in Portland".

Geocoding and forecasts are recorded Open-Meteo responses (see conftest).
"""

from datetime import UTC, datetime

import pytest

from coco_hub.intents import weather as weather_intents
from coco_hub.intents.engine import IntentContext, IntentRegistry, Match, RulesEngine
from coco_hub.protocol import AnswerView, UtteranceContext, UtteranceRequest, WeatherView
from coco_hub.qwen.catalog import Catalog
from coco_hub.weather.places import Place, choose, parse_results

from .conftest import Clock, FakeOpenMeteo, load

pytestmark = pytest.mark.anyio

# Five minutes after the Portland recording's "current" (12:00 in Portland, Oregon).
PORTLAND_NOW = datetime(2026, 11, 16, 20, 5, tzinfo=UTC)


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    weather_intents.register(r)
    return r


def request(text: str) -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        source="voice",
        context=UtteranceContext(view="idle", alarm_ringing=False, media_playing=None),
    )


@pytest.fixture
def portland_clock() -> Clock:
    return Clock(PORTLAND_NOW)


@pytest.fixture
def ask(registry, home, portland_clock, make_service):
    """``await ask(text)`` -> (Action, FakeOpenMeteo), forecasts served from Portland's."""

    async def ask(text: str, settings=None, fake=None, slots=None, intent=None):
        settings = settings or home
        fake = fake or FakeOpenMeteo("portland_metric")
        service = make_service(settings, fake)
        ctx = IntentContext(settings=settings, clock=portland_clock, services={"weather": service})
        engine = RulesEngine(registry)
        try:
            if intent is not None:
                match = Match(intent=intent, slots=slots or {}, text=text)
                action = await engine.dispatch(match, request(text), ctx, matched_by="qwen")
            else:
                action = await engine.handle(request(text), ctx)
        finally:
            await service.aclose()
        return action, fake

    return ask


# --- choosing among geocoding results ---------------------------------------------------


def portland_results() -> list[Place]:
    return parse_results(load("geocode_portland"))


def test_parse_results_reads_the_recording():
    places = portland_results()
    assert len(places) == 10
    first = places[0]
    assert (first.name, first.admin1, first.country_code) == ("Portland", "Oregon", "US")
    assert first.timezone == "America/Los_Angeles"
    assert (first.lat, first.lon) == (45.52345, -122.67621)


def test_parse_results_with_no_match():
    assert parse_results(load("geocode_none")) == []


def test_bare_name_picks_the_biggest_and_says_it_was_ambiguous():
    pick = choose(portland_results(), qualifier=None)
    assert pick is not None
    assert pick.place.label == "Portland, Oregon"
    assert pick.ambiguous


def test_qualifier_prefers_what_was_said():
    pick = choose(portland_results(), qualifier="maine")
    assert pick is not None
    assert pick.place.label == "Portland, Maine"
    assert not pick.ambiguous


def test_qualifier_can_be_a_country():
    pick = choose(portland_results(), qualifier="England")
    assert pick is not None
    assert (pick.place.admin1, pick.place.country_code) == ("England", "GB")
    assert not pick.ambiguous


def test_a_clear_favourite_is_not_ambiguous():
    pick = choose(parse_results(load("geocode_paris")), qualifier=None)
    assert pick is not None
    assert pick.place.label == "Paris, France"
    assert not pick.ambiguous


def test_unknown_qualifier_falls_back_to_the_biggest_and_says_so():
    pick = choose(portland_results(), qualifier="narnia")
    assert pick is not None
    assert pick.place.label == "Portland, Oregon"
    assert pick.ambiguous


def test_nothing_to_choose():
    assert choose([], qualifier=None) is None


# --- answering --------------------------------------------------------------------------


async def test_forecast_today_in_portland(ask):
    # The kitchen's utterance from the issue.
    action, fake = await ask("What's the forecast today in Portland")
    assert action.intent == "weather.forecast"
    assert action.say.startswith(
        "There's more than one Portland, so here's Portland, Oregon. Today, expect "
    )
    assert fake.searches == ["portland"]
    # Portland's forecast, and home's to compare with (#111).
    forecast, _home = sorted(fake.forecasts, key=lambda r: r.url.params["latitude"] != "45.52345")
    assert forecast.url.params["latitude"] == "45.52345"
    assert forecast.url.params["longitude"] == "-122.67621"
    assert forecast.url.params["timezone"] == "America/Los_Angeles"
    assert isinstance(action.view, WeatherView)
    assert action.view.place == "Portland, Oregon"
    assert action.view.current.temp == 10.4


async def test_saying_the_state_picks_it(ask):
    action, fake = await ask("will it rain tomorrow in portland maine")
    assert action.intent == "weather.rain"
    assert action.say.startswith("Here's Portland, Maine. ")
    # The whole phrase finds nothing, so the Hub searches the name and filters by the state.
    assert fake.searches == ["portland maine", "portland"]
    assert fake.forecasts[0].url.params["timezone"] == "America/New_York"
    assert action.view.place == "Portland, Maine"


async def test_unambiguous_place_is_named(ask):
    action, _ = await ask("what's the weather in paris")
    assert action.intent == "weather.current"
    assert action.say.startswith("Here's Paris, France. It's 10 degrees and overcast.")


async def test_qwen_place_slot_with_a_comma(ask):
    catalog = Catalog.from_registry(_registry_with_weather())
    spec = catalog.intents["weather.forecast"].llm_slots["place"]
    key, value = spec.handler_slot("place", "Portland, Maine")
    action, fake = await ask(
        "what's it like in portland maine tomorrow",
        intent="weather.forecast",
        slots={key: value, "when": "tomorrow"},
    )
    assert action.matched_by == "qwen"
    assert action.say.startswith("Here's Portland, Maine. Tomorrow, expect ")
    assert fake.searches == ["Portland"]


async def test_place_not_found(ask):
    action, fake = await ask("what's the weather in atlantis xyzzy")
    assert action.say == "Sorry, I couldn't find a place called Atlantis Xyzzy."
    assert action.view == AnswerView(text=action.say)
    assert fake.forecasts == []


async def test_geocoding_unreachable(ask):
    fake = FakeOpenMeteo("portland_metric")
    fake.fail = True
    action, _ = await ask("what's the weather in paris", fake=fake)
    assert action.say == "Sorry, I can't reach the weather service right now."


async def test_a_place_works_without_a_home_location(ask, settings):
    action, _ = await ask("what's the weather in paris", settings=settings)
    assert action.say.startswith("Here's Paris, France. It's ")


async def test_home_weather_has_no_place(ask):
    action, fake = await ask("what's the weather")
    assert action.view.place is None
    assert fake.searches == []
    assert not action.say.startswith("Here's")


async def test_places_are_looked_up_once(registry, home, portland_clock, make_service):
    fake = FakeOpenMeteo("portland_metric")
    service = make_service(home, fake)
    ctx = IntentContext(settings=home, clock=portland_clock, services={"weather": service})
    engine = RulesEngine(registry)
    try:
        await engine.handle(request("what's the weather in paris"), ctx)
        await engine.handle(request("will it rain in paris"), ctx)
    finally:
        await service.aclose()
    assert fake.searches == ["paris"]
    # Paris once, and home once (to compare with, #111): both cached.
    assert len(fake.forecasts) == 2


def _registry_with_weather() -> IntentRegistry:
    r = IntentRegistry()
    weather_intents.register(r)
    return r
