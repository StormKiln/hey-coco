from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from coco_hub.weather import codes
from coco_hub.weather.spoken import (
    a_or_an,
    degrees,
    part_of_day,
    round_temp,
    spoken_hour,
    spoken_temp,
)

TORONTO = ZoneInfo("America/Toronto")


@pytest.mark.parametrize(
    ("value", "rounded"),
    [(3.4, 3), (3.5, 4), (4.5, 5), (-0.1, 0), (-0.0, 0), (-0.5, -1), (-1.5, -2), (-2.4, -2)],
)
def test_round_temp_rounds_half_away_from_zero(value, rounded):
    assert round_temp(value) == rounded


@pytest.mark.parametrize(
    ("value", "spoken"),
    [(3.2, "3"), (-1.0, "minus 1"), (-0.2, "0"), (-12.6, "minus 13"), (72.5, "73")],
)
def test_spoken_temp(value, spoken):
    assert spoken_temp(value) == spoken


@pytest.mark.parametrize(
    ("value", "spoken"),
    [
        (3, "3 degrees"),
        (1.2, "1 degree"),
        (-1, "minus 1 degree"),
        (0, "0 degrees"),
        (-7, "minus 7 degrees"),
    ],
)
def test_degrees(value, spoken):
    assert degrees(value) == spoken


@pytest.mark.parametrize(
    ("n", "article"),
    [(8, "an"), (80, "an"), (18, "an"), (11, "an"), (70, "a"), (100, "a"), (1, "a")],
)
def test_a_or_an(n, article):
    assert a_or_an(n) == article


@pytest.mark.parametrize(
    ("hour", "spoken"), [(0, "midnight"), (12, "noon"), (9, "9 AM"), (14, "2 PM"), (23, "11 PM")]
)
def test_spoken_hour(hour, spoken):
    assert spoken_hour(datetime(2026, 11, 14, hour, tzinfo=TORONTO)) == spoken


NOW = datetime(2026, 11, 14, 10, 20, tzinfo=TORONTO)


@pytest.mark.parametrize(
    ("when", "phrase"),
    [
        (datetime(2026, 11, 14, 11, tzinfo=TORONTO), "this morning"),
        (datetime(2026, 11, 14, 13, tzinfo=TORONTO), "this afternoon"),
        (datetime(2026, 11, 14, 18, tzinfo=TORONTO), "this evening"),
        (datetime(2026, 11, 14, 22, tzinfo=TORONTO), "tonight"),
        (datetime(2026, 11, 15, 2, tzinfo=TORONTO), "overnight"),
        (datetime(2026, 11, 15, 8, tzinfo=TORONTO), "tomorrow morning"),
        (datetime(2026, 11, 15, 15, tzinfo=TORONTO), "tomorrow afternoon"),
    ],
)
def test_part_of_day(when, phrase):
    assert part_of_day(when, NOW) == phrase


@pytest.mark.parametrize(
    ("code", "description"),
    [
        (0, "Clear"),
        (1, "Mostly clear"),
        (2, "Partly cloudy"),
        (3, "Overcast"),
        (45, "Fog"),
        (51, "Light drizzle"),
        (61, "Light rain"),
        (63, "Rain"),
        (65, "Heavy rain"),
        (66, "Light freezing rain"),
        (71, "Light snow"),
        (75, "Heavy snow"),
        (80, "Light showers"),
        (86, "Snow showers"),
        (95, "Thunderstorm"),
        (99, "Thunderstorm with hail"),
        (42, "Unknown"),
    ],
)
def test_wmo_descriptions(code, description):
    assert codes.describe(code) == description


def test_every_known_code_has_phrases():
    for code in codes.KNOWN_CODES:
        assert codes.condition_phrase(code).startswith(("and ", "with "))
        assert codes.sky_phrase(code)


@pytest.mark.parametrize(
    ("code", "kind"),
    [(3, None), (51, "rain"), (63, "rain"), (67, "rain"), (73, "snow"), (85, "snow"), (95, "rain")],
)
def test_precip_kind(code, kind):
    assert codes.precip_kind(code) == kind
