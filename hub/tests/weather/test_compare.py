"""A named place's weather compared with home (#111): "what's the forecast today in Portland"
answers for Portland, adds one phrase about home, and the view carries home's numbers for
the same periods.

Home is Ottawa (``ottawa_metric``, recorded 2026-11-14, days 11-14..11-20); the place is
Portland, Oregon (``portland_metric``, recorded 2026-11-16, days 11-16..11-22). The days
both cover (11-16..11-20) are compared.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from coco_hub.intents import weather as weather_intents
from coco_hub.intents.engine import IntentContext, Match, RulesEngine
from coco_hub.protocol import WeatherNow, WeatherView
from coco_hub.weather.model import Forecast, Hour
from coco_hub.weather.places import distance_km

from .conftest import FakeOpenMeteo
from .test_places import portland_clock, registry, request  # noqa: F401 (fixtures)

pytestmark = pytest.mark.anyio

HOME_LAT = "45.42"


def fake_with_home() -> FakeOpenMeteo:
    fake = FakeOpenMeteo("portland_metric")
    fake.at[HOME_LAT] = "ottawa_metric"
    return fake


@pytest.fixture
def ask(registry, home, portland_clock, make_service):  # noqa: F811 (fixtures)
    """``await ask(text)`` -> (Action, FakeOpenMeteo): Portland's forecast, Ottawa's at home."""

    async def ask(text: str, settings=None, fake=None):
        settings = settings or home
        fake = fake or fake_with_home()
        service = make_service(settings, fake)
        ctx = IntentContext(settings=settings, clock=portland_clock, services={"weather": service})
        try:
            action = await RulesEngine(registry).handle(request(text), ctx)
        finally:
            await service.aclose()
        return action, fake

    return ask


async def test_forecast_today_compares_the_high_with_home(ask):
    action, fake = await ask("What's the forecast today in Portland")
    assert action.say.startswith("There's more than one Portland, so here's Portland, Oregon. ")
    # Portland's high 10.8 (11) vs Ottawa's 7.7 (8) on 11-16.
    assert action.say.endswith(" That's 3 degrees warmer than at home.")
    assert len(fake.forecasts) == 2
    assert isinstance(action.view, WeatherView)
    compare = action.view.compare
    assert compare is not None
    assert compare.name == "Home"
    assert compare.current.temp == 4.1
    # Home's days for the place's dates, as far as home's forecast goes.
    assert [d.date for d in compare.daily] == [
        "2026-11-16",
        "2026-11-17",
        "2026-11-18",
        "2026-11-19",
        "2026-11-20",
    ]
    assert [d.date for d in action.view.daily][:5] == [d.date for d in compare.daily]
    assert compare.daily[0].high == 7.7


async def test_current_compares_now_with_now(ask):
    action, _ = await ask("what's the weather in portland")
    # 10.4 (10) in Portland vs 4.1 (4) at home.
    assert action.say.endswith(" That's 6 degrees warmer than at home.")


async def test_about_the_same(ask):
    action, _ = await ask("what's the forecast for tomorrow in portland")
    # Highs 10.7 vs 11.3: both 11.
    assert action.say.endswith(" About the same temperature as at home.")


async def test_rain_compares_rain(ask):
    action, _ = await ask("will it rain tomorrow in portland oregon")
    assert action.say.startswith("Here's Portland, Oregon. Yes, rain is likely tomorrow")
    assert action.say.endswith(" It's dry at home.")


async def test_low_tonight_compares_the_low(ask):
    action, _ = await ask("what's the low tonight in portland")
    # Tonight (18:00-06:00 local on 11-16) in each place: 5.8 (6) vs 1.9 (2).
    assert action.say.endswith("Tonight's low is 6 degrees. That's 4 degrees warmer than at home.")


async def test_no_comparison_when_the_place_is_home(ask, home):
    portland_home = home.model_copy(
        update={
            "timezone": "America/Los_Angeles",
            "location": home.location.model_copy(update={"lat": 45.52, "lon": -122.68}),
        }
    )
    action, fake = await ask("what's the weather in portland", settings=portland_home)
    assert action.view.compare is None
    assert "at home" not in action.say
    assert len(fake.forecasts) == 1


async def test_no_comparison_without_a_home_location(ask, settings):
    action, fake = await ask("what's the weather in portland", settings=settings)
    assert action.view.compare is None
    assert "at home" not in action.say
    assert len(fake.forecasts) == 1


async def test_no_comparison_when_home_weather_is_unavailable(ask):
    fake = fake_with_home()
    fake.at[HOME_LAT] = 503  # home's forecast fails; Portland's still answers
    action, _ = await ask("what's the weather in portland", fake=fake)
    assert action.say.startswith("There's more than one Portland")
    assert action.view.compare is None


async def test_rain_today_maybe_there_dry_here(ask):
    action, _ = await ask("will it rain today in portland")
    assert action.say.endswith("There's a 34 percent chance of rain today. It's dry at home.")


async def test_no_comparison_beyond_home_forecast(ask):
    # Portland's weekend (11-21, 11-22) is past the end of home's recorded forecast.
    action, _ = await ask("what's the weather this weekend in portland")
    assert "at home" not in action.say


async def test_home_weather_has_no_comparison(ask):
    action, _ = await ask("what's the weather")
    assert action.view.compare is None


def test_distance_km():
    assert distance_km(45.52, -122.68, 45.52, -122.68) == 0
    # Portland, Oregon to Ottawa: about 3,600 km.
    assert 3500 < distance_km(45.52345, -122.67621, 45.42, -75.69) < 3700
    # Within SAME_PLACE_KM: downtown Ottawa to the recorded home point.
    assert distance_km(45.4215, -75.6972, 45.42, -75.69) < 1


# --- phrases ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("place", "home", "phrase"),
    [
        (10.4, 4.1, "That's 6 degrees warmer than at home."),
        (-3.2, 2.0, "That's 5 degrees colder than at home."),
        (5.4, 4.4, "That's 1 degree warmer than at home."),
        (10.7, 11.3, "About the same temperature as at home."),
    ],
)
def test_than_home(place, home, phrase):
    assert weather_intents.than_home(place, home) == phrase


def _forecast(tz: str, precip: int, code: int) -> Forecast:
    """Two flat days from 2026-11-16 in ``tz``: 5 degrees, ``precip`` percent every hour."""
    start = datetime(2026, 11, 16, tzinfo=ZoneInfo(tz))
    now = WeatherNow(
        time=start, temp=5, feels_like=5, unit="C", code=code, description="", is_day=True
    )
    hours = tuple(Hour(start + timedelta(hours=i), 5, 5, code, precip, True) for i in range(48))
    return Forecast(current=now, hours=hours, days=(), unit="C")


@pytest.mark.parametrize(
    ("place_p", "home_p", "home_code", "phrase"),
    [
        (80, 70, 61, "Rain is likely at home too."),
        (10, 70, 61, "Rain is likely at home, though."),
        (10, 60, 73, "Snow is likely at home, though."),
        (10, 5, 3, "Dry at home too."),
        (80, 5, 3, "It's dry at home."),
        (80, 30, 61, "At home, there's a 30 percent chance."),
    ],
)
def test_compare_rain(place_p, home_p, home_code, phrase):
    place = _forecast("America/Los_Angeles", place_p, 61)
    home = _forecast("America/Toronto", home_p, home_code)
    now = datetime(2026, 11, 16, 9, tzinfo=ZoneInfo("America/Los_Angeles"))
    # Home is compared at the same wall-clock time on the same date (the module doc).
    home_now = now.replace(tzinfo=ZoneInfo("America/Toronto"))
    match = Match(intent="weather.rain", slots={"when": "today"}, text="will it rain")
    assert weather_intents.compare_rain(match, place, now, home, home_now) == phrase
