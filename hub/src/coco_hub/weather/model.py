"""A parsed forecast for home, independent of the provider."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from coco_hub.protocol import TemperatureUnit, WeatherDay, WeatherHour, WeatherNow
from coco_hub.weather import codes
from coco_hub.weather.spoken import floor_hour

# An hour or day counts as wet when precipitation is at least this likely.
WET_PERCENT = 30


@dataclass(frozen=True)
class Hour:
    time: datetime
    """Start of the hour, timezone-aware."""
    temp: float
    feels_like: float
    code: int
    precip_probability: int
    is_day: bool

    def to_protocol(self, unit: TemperatureUnit) -> WeatherHour:
        return WeatherHour(
            time=self.time,
            temp=self.temp,
            unit=unit,
            code=self.code,
            precip_probability=self.precip_probability,
        )


@dataclass(frozen=True)
class Day:
    date: date
    """Hub-local calendar date."""
    high: float
    low: float
    code: int
    precip_probability: int

    def to_protocol(self, unit: TemperatureUnit) -> WeatherDay:
        return WeatherDay(
            date=self.date.isoformat(),
            high=self.high,
            low=self.low,
            unit=unit,
            code=self.code,
            precip_probability=self.precip_probability,
        )


@dataclass(frozen=True)
class Forecast:
    current: WeatherNow
    hours: tuple[Hour, ...]
    """Hourly, ascending, from local midnight today."""
    days: tuple[Day, ...]
    """Daily, ascending, from today."""
    unit: TemperatureUnit

    def hours_between(self, start: datetime, end: datetime) -> list[Hour]:
        """Hours starting in ``[start, end)``."""
        return [h for h in self.hours if start <= h.time < end]

    def hours_from(self, now: datetime, count: int) -> list[Hour]:
        """``count`` hours from the start of the hour containing ``now``."""
        start = floor_hour(now)
        return [h for h in self.hours if h.time >= start][:count]

    def hourly_after(self, now: datetime, count: int) -> list[WeatherHour]:
        """The ``count`` whole hours after the current one (``now`` is shown as current)."""
        start = floor_hour(now)
        upcoming = [h for h in self.hours if h.time > start][:count]
        return [h.to_protocol(self.unit) for h in upcoming]

    def day(self, on: date) -> Day | None:
        return next((d for d in self.days if d.date == on), None)

    def daily_from(self, today: date, count: int) -> list[WeatherDay]:
        return [d.to_protocol(self.unit) for d in self.days if d.date >= today][:count]


def representative_code(hours: Sequence[Hour]) -> int | None:
    """One code that sums up a stretch of hours for speech.

    The most severe precipitation code among hours where it's reasonably likely; otherwise
    the most common dry code (ties go to the cloudier one). A stray low-probability drizzle
    hour doesn't make a whole afternoon "drizzly".
    """
    if not hours:
        return None
    wet = [
        h.code for h in hours if codes.precip_kind(h.code) and h.precip_probability >= WET_PERCENT
    ]
    if wet:
        return max(wet)
    dry = [h.code for h in hours if not codes.precip_kind(h.code)] or [h.code for h in hours]
    counts = Counter(dry)
    return max(counts, key=lambda c: (counts[c], c))
