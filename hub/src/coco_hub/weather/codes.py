"""WMO weather interpretation codes (as used by Open-Meteo) → words.

Each code has a short ``description`` for the screen ("Light rain"), a ``condition`` phrase
that follows a temperature ("It's 4 degrees *with light rain*") and a ``sky`` phrase used after
"expect" ("Tonight, expect *clear skies*").
"""

from typing import Literal, NamedTuple

PrecipKind = Literal["rain", "snow"]


class _Words(NamedTuple):
    description: str
    condition: str
    sky: str


_CODES: dict[int, _Words] = {
    0: _Words("Clear", "and clear", "clear skies"),
    1: _Words("Mostly clear", "and mostly clear", "mostly clear skies"),
    2: _Words("Partly cloudy", "and partly cloudy", "partly cloudy skies"),
    3: _Words("Overcast", "and overcast", "overcast skies"),
    45: _Words("Fog", "and foggy", "fog"),
    48: _Words("Freezing fog", "with freezing fog", "freezing fog"),
    51: _Words("Light drizzle", "with light drizzle", "light drizzle"),
    53: _Words("Drizzle", "with drizzle", "drizzle"),
    55: _Words("Heavy drizzle", "with heavy drizzle", "heavy drizzle"),
    56: _Words("Light freezing drizzle", "with light freezing drizzle", "light freezing drizzle"),
    57: _Words("Freezing drizzle", "with freezing drizzle", "freezing drizzle"),
    61: _Words("Light rain", "with light rain", "light rain"),
    63: _Words("Rain", "with rain", "rain"),
    65: _Words("Heavy rain", "with heavy rain", "heavy rain"),
    66: _Words("Light freezing rain", "with light freezing rain", "light freezing rain"),
    67: _Words("Freezing rain", "with freezing rain", "freezing rain"),
    71: _Words("Light snow", "with light snow", "light snow"),
    73: _Words("Snow", "with snow", "snow"),
    75: _Words("Heavy snow", "with heavy snow", "heavy snow"),
    77: _Words("Snow grains", "with snow grains", "snow grains"),
    80: _Words("Light showers", "with light showers", "light showers"),
    81: _Words("Showers", "with showers", "showers"),
    82: _Words("Heavy showers", "with heavy showers", "heavy showers"),
    85: _Words("Light snow showers", "with light snow showers", "light snow showers"),
    86: _Words("Snow showers", "with snow showers", "snow showers"),
    95: _Words("Thunderstorm", "with thunderstorms", "thunderstorms"),
    96: _Words("Thunderstorm with hail", "with thunderstorms and hail", "thunderstorms and hail"),
    99: _Words("Thunderstorm with hail", "with thunderstorms and hail", "thunderstorms and hail"),
}
_UNKNOWN = _Words("Unknown", "", "mixed conditions")

KNOWN_CODES = frozenset(_CODES)
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})


def describe(code: int) -> str:
    """Short description for display: ``Partly cloudy``."""
    return _CODES.get(code, _UNKNOWN).description


def condition_phrase(code: int) -> str:
    """``and overcast`` / ``with light rain``; empty for an unknown code."""
    return _CODES.get(code, _UNKNOWN).condition


def sky_phrase(code: int) -> str:
    """What to expect: ``clear skies`` / ``light rain``."""
    return _CODES.get(code, _UNKNOWN).sky


def precip_kind(code: int) -> PrecipKind | None:
    """``snow`` for snowy codes, ``rain`` for any other precipitation, else None."""
    if code in SNOW_CODES:
        return "snow"
    if code >= 51 and code in KNOWN_CODES:
        return "rain"
    return None
