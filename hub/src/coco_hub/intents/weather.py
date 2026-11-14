"""Weather intents: current conditions, forecasts, rain, and highs/lows for home.

All answer with a spoken summary (rounded temperatures, "minus" for negatives, "degrees"
without a unit) and the ``weather`` view. They need the ``weather`` service and a home location.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from statistics import fmean

from coco_hub.intents.builtin import answer
from coco_hub.intents.engine import (
    Intent,
    IntentContext,
    IntentRegistry,
    LlmSlot,
    Match,
    Reply,
)
from coco_hub.protocol import UtteranceRequest, WeatherView
from coco_hub.weather import codes
from coco_hub.weather.model import WET_PERCENT, Day, Forecast, Hour, representative_code
from coco_hub.weather.service import WeatherService
from coco_hub.weather.spoken import (
    a_or_an,
    degrees,
    floor_hour,
    part_of_day,
    round_temp,
    spoken_hour,
    spoken_temp,
)

NO_LOCATION = "I don't know where home is yet — set the location in the Hub settings."
UNREACHABLE = "Sorry, I can't reach the weather service right now."

VIEW_HOURS = 12
VIEW_DAYS = 7
LIKELY_PERCENT = 50
POSSIBLE_PERCENT = 25
OUTLOOK_HOURS = 12
FEELS_LIKE_GAP = 2

# --- When ------------------------------------------------------------------------------

WHEN = (
    r"(?P<when>today|tonight|later(?: today)?|this (?:morning|afternoon|evening|weekend)"
    r"|(?:the )?weekend|tomorrow(?: (?:morning|afternoon|evening|night))?)"
)
# Parts of a day as [start, end) hours from that day's midnight.
_PARTS = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 21), "night": (18, 30)}


@dataclass(frozen=True)
class Period:
    label: str
    """How to say it: ``today``, ``this afternoon``, ``tomorrow night``."""
    start: datetime
    end: datetime
    dates: tuple[date, ...] = ()
    """Whole days covered (today, tomorrow, the weekend); empty for part of a day."""


def _midnight(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def _part(day: date, part: str, label: str, tz: tzinfo) -> Period:
    start_h, end_h = _PARTS[part]
    base = _midnight(day, tz)
    return Period(label, base + timedelta(hours=start_h), base + timedelta(hours=end_h))


def _days(days: list[date], label: str, tz: tzinfo) -> Period:
    return Period(
        label, _midnight(days[0], tz), _midnight(days[-1] + timedelta(days=1), tz), tuple(days)
    )


def resolve_period(when: str | None, now: datetime) -> Period:
    """``when`` slot (or None for today) → the stretch of time it means, Hub-local."""
    tz = now.tzinfo
    assert tz is not None
    today = now.date()
    tomorrow = today + timedelta(days=1)
    when = (when or "today").removeprefix("this ").removeprefix("the ")
    if when in ("today", "later", "later today"):
        return _days([today], "today", tz)
    if when == "tonight":
        # Just after midnight, "tonight" is still the night that started yesterday.
        night_of = today - timedelta(days=1) if now.hour < 5 else today
        return _part(night_of, "night", "tonight", tz)
    if when in ("morning", "afternoon", "evening"):
        return _part(today, when, f"this {when}", tz)
    if when == "tomorrow":
        return _days([tomorrow], "tomorrow", tz)
    if when.startswith("tomorrow "):
        return _part(tomorrow, when.removeprefix("tomorrow "), when, tz)
    if when == "weekend":
        weekday = today.weekday()  # Monday 0 … Sunday 6
        if weekday == 6:
            return _days([today], "this weekend", tz)
        saturday = today + timedelta(days=(5 - weekday) % 7)
        return _days([saturday, saturday + timedelta(days=1)], "this weekend", tz)
    raise ValueError(f"unknown period {when!r}")


def _period_hours(forecast: Forecast, period: Period, now: datetime) -> list[Hour]:
    """Hours in ``period`` from the current hour on; the whole period if it's already over."""
    remaining = forecast.hours_between(max(period.start, floor_hour(now)), period.end)
    return remaining or forecast.hours_between(period.start, period.end)


def day_label(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return f"{d:%A}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _no_forecast(label: str) -> str:
    return f"I don't have a forecast for {label} yet."


def _chance(p: int, kind: str) -> str:
    return f"There's {a_or_an(p)} {p} percent chance of {kind}."


# --- Handlers --------------------------------------------------------------------------

WeatherAnswer = Callable[[Match, Forecast, datetime], str]


def weather_view(forecast: Forecast, now: datetime) -> WeatherView:
    return WeatherView(
        current=forecast.current,
        hourly=forecast.hourly_after(now, VIEW_HOURS),
        daily=forecast.daily_from(now.date(), VIEW_DAYS),
    )


def _handler(say: WeatherAnswer) -> Callable[..., object]:
    """Wrap a ``(match, forecast, now) -> say`` function: fetch weather, attach the view."""

    async def handle(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
        service: WeatherService | None = ctx.services.get("weather")
        if service is None or not service.configured:
            return answer(NO_LOCATION)
        snapshot = await service.get()
        if snapshot is None:
            return answer(UNREACHABLE)
        now = ctx.now()
        return Reply(
            say=say(match, snapshot.forecast, now), view=weather_view(snapshot.forecast, now)
        )

    handle.__name__ = say.__name__
    return handle


def say_current(match: Match, forecast: Forecast, now: datetime) -> str:
    cur = forecast.current
    condition = codes.condition_phrase(cur.code)
    parts = [f"It's {degrees(cur.temp)}{' ' + condition if condition else ''}."]
    if abs(round_temp(cur.feels_like) - round_temp(cur.temp)) >= FEELS_LIKE_GAP:
        parts.append(f"Feels like {spoken_temp(cur.feels_like)}.")
    upcoming = forecast.hours_from(now, OUTLOOK_HOURS)
    wet = next((h for h in upcoming if h.precip_probability >= LIKELY_PERCENT), None)
    if wet is not None:
        kind = codes.precip_kind(wet.code) or "rain"
        parts.append(f"{_cap(kind)} likely {part_of_day(wet.time.astimezone(now.tzinfo), now)}.")
    return " ".join(parts)


def _say_day(forecast: Forecast, day: Day, label: str, tz: tzinfo) -> str:
    hours = forecast.hours_between(
        _midnight(day.date, tz), _midnight(day.date, tz) + timedelta(days=1)
    )
    code = representative_code(hours) if hours else day.code
    assert code is not None
    text = (
        f"{_cap(label)}, expect {codes.sky_phrase(code)}, "
        f"with a high of {spoken_temp(day.high)} and a low of {spoken_temp(day.low)}."
    )
    if day.precip_probability >= WET_PERCENT:
        kind = codes.precip_kind(code) or codes.precip_kind(day.code) or "rain"
        text += " " + _chance(day.precip_probability, kind)
    return text


def say_forecast(match: Match, forecast: Forecast, now: datetime) -> str:
    period = resolve_period(match.slots.get("when"), now)
    tz = now.tzinfo
    assert tz is not None
    if period.dates:
        found = [(d, forecast.day(d)) for d in period.dates]
        days = [(d, day) for d, day in found if day is not None]
        if not days:
            return _no_forecast(period.label)
        return " ".join(_say_day(forecast, day, day_label(d, now.date()), tz) for d, day in days)

    hours = _period_hours(forecast, period, now)
    code = representative_code(hours)
    if code is None:
        return _no_forecast(period.label)
    if period.label.endswith(("tonight", "night")):
        temps = f"with a low of {spoken_temp(min(h.temp for h in hours))}"
    else:
        temps = f"around {degrees(fmean(h.temp for h in hours))}"
    text = f"{_cap(period.label)}, expect {codes.sky_phrase(code)}, {temps}."
    wettest = max(hours, key=lambda h: h.precip_probability)
    if wettest.precip_probability >= WET_PERCENT:
        kind = codes.precip_kind(code) or codes.precip_kind(wettest.code) or "rain"
        text += " " + _chance(wettest.precip_probability, kind)
    return text


def say_rain(match: Match, forecast: Forecast, now: datetime) -> str:
    period = resolve_period(match.slots.get("when"), now)
    hours = _period_hours(forecast, period, now)
    if not hours:
        return _no_forecast(period.label)
    asked = "snow" if "snow" in match.text else "rain"
    wettest = max(hours, key=lambda h: h.precip_probability)
    p = wettest.precip_probability
    kind = codes.precip_kind(wettest.code) or asked
    if p >= LIKELY_PERCENT:
        first = next(h for h in hours if h.precip_probability >= LIKELY_PERCENT)
        text = f"Yes, {kind} is likely {period.label}"
        if first.time > now:
            text += f", starting around {spoken_hour(first.time.astimezone(now.tzinfo))}"
        return f"{text}. About {a_or_an(p)} {p} percent chance."
    if p >= POSSIBLE_PERCENT:
        return f"Maybe. There's {a_or_an(p)} {p} percent chance of {kind} {period.label}."
    return f"No {asked} expected {period.label}."


_HIGH_WORDS = {"high", "warm", "hot"}
_LOW_WORDS = {"low", "cold"}


def say_high_low(match: Match, forecast: Forecast, now: datetime) -> str:
    which = match.slots.get("which", "high")
    want_high = which in _HIGH_WORDS or which == "high and low"
    want_low = which in _LOW_WORDS or which == "high and low"
    period = resolve_period(match.slots.get("when"), now)

    ranges: list[tuple[str, float, float]] = []  # (label, high, low)
    if period.dates:
        for d in period.dates:
            day = forecast.day(d)
            if day is not None:
                ranges.append((day_label(d, now.date()), day.high, day.low))
    else:
        hours = _period_hours(forecast, period, now)
        if hours:
            temps = [h.temp for h in hours]
            ranges.append((period.label, max(temps), min(temps)))
    if not ranges:
        return _no_forecast(period.label)

    sentences = []
    for label, high, low in ranges:
        if want_high and want_low:
            sentences.append(
                f"{_cap(label)}, a high of {spoken_temp(high)} and a low of {spoken_temp(low)}."
            )
        else:
            name, value = ("high", high) if want_high else ("low", low)
            sentences.append(f"{_cap(label)}'s {name} is {degrees(value)}.")
    return " ".join(sentences)


# --- Patterns --------------------------------------------------------------------------

_OUT = r"(?: (?:outside|out there|out))?"
_NOW = r"(?: (?:right )?now)?"
_FEEL = r"(?:cold|warm|hot|chilly|freezing|nice|cool)"
_WILL = r"(?:will it|is it going to|is it gonna|it is going to)"
_PRECIP = r"(?:rain|snow|pour|drizzle|be raining|be snowing)"
_WHICH = r"(?P<which>high and low|high|low)"

CURRENT_PATTERNS = [
    rf"(?:what is |how is )?(?:the )?(?:current )?weather(?: like)?{_OUT}{_NOW}",
    rf"how is it looking{_OUT}{_NOW}",
    rf"how does it look{_OUT}{_NOW}",
    rf"what is it like{_OUT}{_NOW}",
    rf"is it {_FEEL}{_OUT}{_NOW}",
    rf"(?:what is )?(?:the )?(?:current )?(?:temperature|temp){_OUT}{_NOW}",
    rf"how (?:cold|warm|hot) is it{_OUT}{_NOW}",
]
FORECAST_PATTERNS = [
    rf"(?:what is |how is )?the (?:weather|forecast)"
    rf"(?: (?:going to be|gonna be|looking|look))?(?: like)?(?: for)? {WHEN}",
    r"(?:what is )?(?:the )?(?:weather )?forecast",
    rf"(?:weather|forecast)(?: for)? {WHEN}",
    rf"what is {WHEN} looking like",
    rf"what does {WHEN} look like",
    rf"how does {WHEN} look",
    rf"how is {WHEN} looking",
    rf"{_WILL} be (?:cold|warm|hot|chilly|freezing|nice|cool|sunny|cloudy) {WHEN}",
]
RAIN_PATTERNS = [
    rf"{_WILL} {_PRECIP}(?: {WHEN})?",
    rf"(?:do i|will i|should i) (?:need|bring|take) (?:an |my )?"
    rf"(?:umbrella|raincoat|rain coat|rain jacket)(?: {WHEN})?",
    rf"(?:what is )?(?:the )?(?:chance|chances|probability) of (?:rain|snow|precipitation)"
    rf"(?: {WHEN})?",
    rf"is there (?:any )?(?:rain|snow) (?:in the forecast|coming|expected|on the way)"
    rf"(?: {WHEN})?",
    rf"any (?:rain|snow)(?: {WHEN})?",
]
HIGH_LOW_PATTERNS = [
    rf"(?:what is )?the {_WHICH}(?: temperature)?(?: (?:for )?{WHEN})?",
    rf"(?:what is )?(?P<when>today|tomorrow|tonight)s {_WHICH}(?: temperature)?",
    rf"how (?P<which>cold|warm|hot) {_WILL} (?:get|be)(?: {WHEN})?",
]


# --- Qwen fallback slots ----------------------------------------------------------------

# Every value is a period ``resolve_period`` accepts once "_" becomes a space (a test checks).
LLM_WHEN_VALUES = (
    "today",
    "tonight",
    "this_morning",
    "this_afternoon",
    "this_evening",
    "tomorrow",
    "tomorrow_morning",
    "tomorrow_afternoon",
    "tomorrow_evening",
    "tomorrow_night",
    "this_weekend",
)
LLM_WHEN = LlmSlot(
    enum=LLM_WHEN_VALUES,
    to_slot=lambda v: v.replace("_", " "),
    hint=f"when is one of: {', '.join(LLM_WHEN_VALUES)}; leave it out for right now.",
)
LLM_WHICH = LlmSlot(
    enum=("high", "low", "both"),
    to_slot=lambda v: "high and low" if v == "both" else v,
    hint='which is high (warmest, "how hot"), low (coldest, "how cold") or both.',
)


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "weather.current",
            patterns=CURRENT_PATTERNS,
            handler=_handler(say_current),
            description="weather or temperature outside right now",
        )
    )
    registry.register(
        Intent(
            "weather.forecast",
            patterns=FORECAST_PATTERNS,
            handler=_handler(say_forecast),
            description=(
                "what the weather will be like later (today, tonight, tomorrow, the weekend), "
                "including what to wear"
            ),
            llm_slots={"when": LLM_WHEN},
        )
    )
    registry.register(
        Intent(
            "weather.rain",
            patterns=RAIN_PATTERNS,
            handler=_handler(say_rain),
            description="only questions about rain, snow, showers, storms or umbrellas",
            llm_slots={"when": LLM_WHEN},
        )
    )
    registry.register(
        Intent(
            "weather.high_low",
            patterns=HIGH_LOW_PATTERNS,
            handler=_handler(say_high_low),
            description=(
                "the forecast high or low temperature for a day or part of a day "
                "(how hot or how cold it will get), not the temperature right now"
            ),
            llm_slots={"when": LLM_WHEN, "which": LLM_WHICH},
        )
    )
