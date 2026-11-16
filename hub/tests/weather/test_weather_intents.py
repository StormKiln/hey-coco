from datetime import UTC, datetime

import pytest

from coco_hub.intents import INTENT_MODULES, build_registry
from coco_hub.intents import weather as weather_intents
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import AnswerView, UtteranceContext, UtteranceRequest, WeatherView

from .conftest import FakeOpenMeteo

pytestmark = pytest.mark.anyio

CUR, FC, RAIN, HL = "weather.current", "weather.forecast", "weather.rain", "weather.high_low"


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    weather_intents.register(r)
    return r


PHRASES = [
    # weather.current
    ("what's the weather", CUR, {}),
    ("What's the weather like?", CUR, {}),
    ("how's the weather", CUR, {}),
    ("what's the weather like outside", CUR, {}),
    ("what's the weather right now", CUR, {}),
    ("weather", CUR, {}),
    ("Hey Coco, what's the weather?", CUR, {}),
    ("how's it looking outside", CUR, {}),
    ("how does it look outside", CUR, {}),
    ("what's it like outside", CUR, {}),
    ("is it cold out", CUR, {}),
    ("is it cold outside right now", CUR, {}),
    ("is it warm outside", CUR, {}),
    ("is it chilly out there", CUR, {}),
    ("what's the temperature", CUR, {}),
    ("what's the temperature outside", CUR, {}),
    ("what's the current temperature", CUR, {}),
    ("how cold is it outside", CUR, {}),
    ("how warm is it", CUR, {}),
    ("current weather", CUR, {}),
    ("how chilly is it outside", CUR, {}),
    ("how cold is it", CUR, {}),
    ("how freezing is it out there", CUR, {}),
    ("is it warm enough for shorts", CUR, {}),
    ("is it warm enough to go out without a coat", CUR, {}),
    ("is it cold enough for a hat", CUR, {}),
    ("do I need a coat", CUR, {}),
    ("do I need a jacket right now", CUR, {}),
    ("should I wear a sweater", CUR, {}),
    # weather.forecast
    ("what's the weather today", FC, {"when": "today"}),
    ("what's the weather like tomorrow", FC, {"when": "tomorrow"}),
    ("what's the forecast", FC, {}),
    ("what's the forecast for tomorrow", FC, {"when": "tomorrow"}),
    ("what's the weather going to be like this afternoon", FC, {"when": "this afternoon"}),
    ("what's the weather like this weekend", FC, {"when": "this weekend"}),
    ("how's the weather looking tonight", FC, {"when": "tonight"}),
    ("what's tomorrow looking like", FC, {"when": "tomorrow"}),
    ("how does the weekend look", FC, {"when": "the weekend"}),
    ("is it going to be cold tonight", FC, {"when": "tonight"}),
    ("will it be nice this weekend", FC, {"when": "this weekend"}),
    ("weather tomorrow", FC, {"when": "tomorrow"}),
    ("forecast for tomorrow morning", FC, {"when": "tomorrow morning"}),
    ("do I need a jacket today", FC, {"when": "today"}),
    # weather.rain
    ("will it rain today", RAIN, {"when": "today"}),
    ("will it rain this afternoon", RAIN, {"when": "this afternoon"}),
    ("is it going to rain tomorrow", RAIN, {"when": "tomorrow"}),
    ("is it gonna rain", RAIN, {}),
    ("will it snow tomorrow", RAIN, {"when": "tomorrow"}),
    ("do I need an umbrella", RAIN, {}),
    ("do I need an umbrella today", RAIN, {"when": "today"}),
    ("should I bring an umbrella", RAIN, {}),
    ("what's the chance of rain tonight", RAIN, {"when": "tonight"}),
    ("is there any rain in the forecast", RAIN, {}),
    # weather.high_low
    ("what's the high today", HL, {"which": "high", "when": "today"}),
    ("what's the high", HL, {"which": "high"}),
    ("what's today's high", HL, {"when": "today", "which": "high"}),
    ("what's the low tonight", HL, {"which": "low", "when": "tonight"}),
    ("what's tomorrow's high", HL, {"when": "tomorrow", "which": "high"}),
    ("what's the high temperature tomorrow", HL, {"which": "high", "when": "tomorrow"}),
    ("what's the high and low for today", HL, {"which": "high and low", "when": "today"}),
    ("how cold is it going to get tonight", HL, {"which": "cold", "when": "tonight"}),
    ("how warm will it get today", HL, {"which": "warm", "when": "today"}),
    ("how cold is it supposed to get tonight", HL, {"which": "cold", "when": "tonight"}),
    # a named place (#93)
    ("what's the weather in paris", CUR, {"place": "paris"}),
    ("is it cold in paris right now", CUR, {"place": "paris"}),
    ("what's the forecast today in portland", FC, {"when": "today", "place": "portland"}),
    ("What's the forecast today in Portland?", FC, {"when": "today", "place": "portland"}),
    ("what's the weather in paris tomorrow", FC, {"place": "paris", "when": "tomorrow"}),
    (
        "what's the weather like in new york this weekend",
        FC,
        {"place": "new york", "when": "this weekend"},
    ),
    ("what's the forecast for portland oregon", FC, {"place": "portland oregon"}),
    (
        "will it rain tomorrow in portland maine",
        RAIN,
        {"when": "tomorrow", "place": "portland maine"},
    ),
    ("will it snow in st johns tonight", RAIN, {"place": "st johns", "when": "tonight"}),
    (
        "what's the high tomorrow in paris",
        HL,
        {"which": "high", "when": "tomorrow", "place": "paris"},
    ),
    # not weather
    ("will it rain in 1 hour", None, None),
    ("will it rain in the afternoon", None, None),
    ("is it warm in here", None, None),
    ("is it cold in the kitchen", None, None),
    ("set a timer for ten minutes", None, None),
    ("play some rain sounds", None, None),
    ("what's the date", None, None),
    ("how cold is the fridge", None, None),
    ("do I need a coat of paint", None, None),
    ("what time is it", None, None),
]


@pytest.mark.parametrize(("phrase", "intent", "slots"), PHRASES)
def test_phrase_to_intent(registry, phrase, intent, slots):
    match = registry.match(normalize(phrase))
    if intent is None:
        assert match is None
    else:
        assert match is not None, phrase
        assert (match.intent, match.slots) == (intent, slots)


def test_weather_module_is_registered():
    assert weather_intents.register in INTENT_MODULES
    registry = build_registry()
    assert {CUR, FC, RAIN, HL} <= {i.name for i in registry}


def test_weather_phrases_do_not_collide_with_other_modules():
    registry = build_registry()
    for phrase, intent, _ in PHRASES:
        if intent is not None:
            assert registry.match(normalize(phrase)).intent == intent, phrase


def request(text: str) -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        source="voice",
        context=UtteranceContext(view="idle", alarm_ringing=False, media_playing=None),
    )


@pytest.fixture
def ask(registry, home, clock, make_service):
    """``await ask(text, fixture="ottawa_metric", settings=home)`` -> Action."""

    async def ask(text: str, fixture: str = "ottawa_metric", settings=None, fake=None):
        settings = settings or home
        fake = fake or FakeOpenMeteo(fixture)
        service = make_service(settings, fake)
        ctx = IntentContext(settings=settings, clock=clock, services={"weather": service})
        try:
            return await RulesEngine(registry).handle(request(text), ctx)
        finally:
            await service.aclose()

    return ask


SAYS = [
    # current
    ("what's the weather", "ottawa_metric", "It's 4 degrees and overcast. Feels like 0."),
    ("is it cold out", "subzero", "It's minus 6 degrees and overcast. Feels like minus 10."),
    (
        "what's the weather",
        "rain_afternoon",
        "It's 4 degrees and overcast. Feels like 0. Rain likely this afternoon.",
    ),
    # forecast
    (
        "what's the forecast for tomorrow",
        "ottawa_metric",
        "Tomorrow, expect overcast skies, with a high of 5 and a low of 0.",
    ),
    (
        "what's the weather today",
        "ottawa_metric",
        "Today, expect overcast skies, with a high of 4 and a low of 0.",
    ),
    (
        "what's the weather today",
        "rain_afternoon",
        "Today, expect rain, with a high of 4 and a low of 0. There's a 90 percent chance of rain.",
    ),
    (
        "what's the weather like this afternoon",
        "ottawa_metric",
        "This afternoon, expect overcast skies, around 4 degrees.",
    ),
    (
        "what's the weather like tonight",
        "ottawa_metric",
        "Tonight, expect clear skies, with a low of 0.",
    ),
    (
        "what's the forecast for tomorrow",
        "subzero",
        "Tomorrow, expect snow, with a high of minus 5 and a low of minus 10. "
        "There's an 80 percent chance of snow.",
    ),
    (
        "what's the weather like this weekend",
        "ottawa_metric",
        "Today, expect overcast skies, with a high of 4 and a low of 0. "
        "Tomorrow, expect overcast skies, with a high of 5 and a low of 0.",
    ),
    # rain
    ("will it rain today", "ottawa_metric", "No rain expected today."),
    ("do I need an umbrella", "ottawa_metric", "No rain expected today."),
    (
        "will it rain this afternoon",
        "rain_afternoon",
        "Yes, rain is likely this afternoon, starting around noon. About a 90 percent chance.",
    ),
    (
        "do I need an umbrella",
        "rain_afternoon",
        "Yes, rain is likely today, starting around noon. About a 90 percent chance.",
    ),
    ("will it rain tomorrow", "rain_afternoon", "No rain expected tomorrow."),
    (
        "will it snow tomorrow",
        "subzero",
        "Yes, snow is likely tomorrow, starting around noon. About an 80 percent chance.",
    ),
    # high / low
    ("what's the high today", "ottawa_metric", "Today's high is 4 degrees."),
    ("what's tomorrow's high", "ottawa_imperial", "Tomorrow's high is 40 degrees."),
    ("what's the low tonight", "subzero", "Tonight's low is minus 10 degrees."),
    ("what's the high and low for today", "ottawa_metric", "Today, a high of 4 and a low of 0."),
    ("how cold is it going to get tonight", "ottawa_metric", "Tonight's low is 0 degrees."),
    ("what's the low", "subzero", "Today's low is minus 10 degrees."),
    # current conditions for "what to wear right now" questions
    ("is it warm enough for shorts", "ottawa_metric", "It's 4 degrees and overcast. Feels like 0."),
    ("do I need a coat", "subzero", "It's minus 6 degrees and overcast. Feels like minus 10."),
]


@pytest.mark.parametrize(("text", "fixture", "say"), SAYS)
async def test_spoken_replies(ask, text, fixture, say):
    action = await ask(text, fixture)
    assert action.say == say


async def test_imperial_current(ask, home):
    action = await ask(
        "what's the weather", "ottawa_imperial", home.model_copy(update={"units": "imperial"})
    )
    assert action.say == "It's 39 degrees and overcast. Feels like 32."
    assert action.view.current.unit == "F"


@pytest.mark.parametrize("text", ["what's the weather", "will it rain today", "what's the high"])
async def test_weather_view(ask, text):
    action = await ask(text)
    assert isinstance(action.view, WeatherView)
    view = action.view
    assert view.current.temp == 4.1
    assert view.current.description == "Overcast"
    # Hourly starts at the next whole hour (11:00 local = 16:00Z), daily at today.
    assert view.hourly[0].time == datetime(2026, 11, 14, 16, tzinfo=UTC)
    assert len(view.hourly) == 12
    assert [d.date for d in view.daily][:2] == ["2026-11-14", "2026-11-15"]
    assert len(view.daily) == 7
    assert view.daily[1].low == 0.0


async def test_location_not_configured(ask, settings):
    action = await ask("what's the weather", settings=settings)
    expected = "I don't know where home is yet. Set Home location in my Settings."
    assert action.say == expected
    assert action.view == AnswerView(text=expected)
    assert action.intent == CUR


async def test_weather_service_unreachable(ask):
    fake = FakeOpenMeteo()
    fake.fail = True
    action = await ask("will it rain today", fake=fake)
    expected = "Sorry, I can't reach the weather service right now."
    assert action.say == expected
    assert action.view == AnswerView(text=expected)


async def test_beyond_the_forecast(ask, clock):
    clock.advance(days=6)  # tomorrow is past the 7-day recording
    action = await ask("what's the forecast for tomorrow")
    assert action.say == "I don't have a forecast for tomorrow yet."


# --- periods that are over, or partly over -------------------------------------------------


async def test_rain_for_a_part_of_today_that_is_over(ask, clock):
    clock.advance(hours=8)  # 18:20 Saturday: the afternoon's rain (12:00-17:00) is past
    action = await ask("will it rain this afternoon", "rain_afternoon")
    assert action.say == "This afternoon's over — no rain expected tonight."


async def test_morning_over_answers_for_later_today(ask, clock):
    clock.advance(hours=1)  # 11:20 Saturday: the morning is still on
    action = await ask("will it rain this morning", "rain_afternoon")
    assert action.say == "No rain expected this morning."
    clock.advance(hours=1)  # 12:20: the morning is over; rain is on now
    action = await ask("will it rain this morning", "rain_afternoon")
    assert action.say == (
        "This morning's over — yes, rain is likely later today. About a 90 percent chance."
    )


async def test_forecast_for_an_evening_that_is_over(ask, clock):
    clock.advance(hours=11)  # 21:20 Saturday
    action = await ask("what's the weather like this evening")
    assert action.say.startswith("This evening's over — tonight, expect ")


async def test_high_for_an_afternoon_that_is_over(ask, clock):
    clock.advance(hours=8)
    action = await ask("what's the high this afternoon")
    assert action.say.startswith("This afternoon's over — tonight's high is ")


async def test_weekend_on_saturday_evening_is_tonight_and_sunday(ask, clock):
    clock.advance(hours=8)  # 18:20 Saturday
    action = await ask("what's the weather like this weekend")
    assert action.say.startswith("Tonight, expect ")
    assert action.say.endswith("Tomorrow, expect overcast skies, with a high of 5 and a low of 0.")
    assert "Today" not in action.say


async def test_weekend_on_saturday_morning_is_both_days(ask):
    action = await ask("what's the weather like this weekend")
    assert action.say.startswith("Today, expect ")


async def test_resolve_period_weekend_on_saturday_evening():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Toronto")
    saturday_evening = datetime(2026, 11, 14, 18, 20, tzinfo=tz)
    period = weather_intents.resolve_period("this weekend", saturday_evening)
    assert [d.isoformat() for d in period.dates] == ["2026-11-15"]
    assert period.lead is not None and period.lead.label == "tonight"
    assert period.start == datetime(2026, 11, 14, 18, 20, tzinfo=tz).replace(minute=0)
