"""Weather intents: current conditions, forecasts, rain, and highs/lows for home, or for a
place the person names ("what's the forecast today in Portland", #93).

All answer with a spoken summary (rounded temperatures, "minus" for negatives, "degrees"
without a unit) and the ``weather`` view. They need the ``weather`` service, and a home
location unless a place is named. A named place is geocoded (``weather.places``), its forecast
read in its own timezone, and the answer starts by naming it ("Here's Paris, France."), or by
saying which one it picked when the name is ambiguous ("There's more than one Portland, so
here's Portland, Oregon.").
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from statistics import fmean
from zoneinfo import ZoneInfo

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
from coco_hub.weather.client import WeatherFetchError
from coco_hub.weather.model import WET_PERCENT, Day, Forecast, Hour, representative_code
from coco_hub.weather.places import Pick
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

NO_LOCATION = "I don't know where home is yet. Set Home location in my Settings."
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
    lead: "Period | None" = None
    """Part of a day to answer for before ``dates`` (the rest of tonight, for the weekend
    asked on Saturday evening)."""
    over: str | None = None
    """The period asked about, when it was already over and this is the rest of today
    instead (``this afternoon`` asked at 6 PM): answers start "This afternoon's over — "."""


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


# On Saturday from this hour on, "this weekend" means the rest of tonight and Sunday.
WEEKEND_EVENING_HOUR = 17


def _rest_of_today(now: datetime, tz: tzinfo) -> Period:
    """From the current hour to the end of tonight (from 5 PM) or of today."""
    start = floor_hour(now)
    if now.hour >= _PARTS["evening"][0]:
        night = _part(now.date(), "night", "tonight", tz)
        return Period("tonight", start, night.end)
    return Period("later today", start, _midnight(now.date() + timedelta(days=1), tz))


def resolve_period(when: str | None, now: datetime) -> Period:
    """``when`` slot (or None for today) → the stretch of time it means, Hub-local.

    A part of today that is already over ("this afternoon" at 6 PM) becomes the rest of
    today, marked ``over``; "this weekend" on Saturday evening is tonight (``lead``) and
    Sunday.
    """
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
        part = _part(today, when, f"this {when}", tz)
        if now >= part.end:
            return replace(_rest_of_today(now, tz), over=part.label)
        return part
    if when == "tomorrow":
        return _days([tomorrow], "tomorrow", tz)
    if when.startswith("tomorrow "):
        return _part(tomorrow, when.removeprefix("tomorrow "), when, tz)
    if when == "weekend":
        weekday = today.weekday()  # Monday 0 … Sunday 6
        if weekday == 6:
            return _days([today], "this weekend", tz)
        if weekday == 5 and now.hour >= WEEKEND_EVENING_HOUR:
            sunday = today + timedelta(days=1)
            lead = _rest_of_today(now, tz)
            return Period(
                "this weekend",
                lead.start,
                _midnight(sunday + timedelta(days=1), tz),
                (sunday,),
                lead=lead,
            )
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


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:]


def _over(period: Period, text: str) -> str:
    """Say that the period asked about is over before answering for the rest of today."""
    if period.over is None:
        return text
    return f"{_cap(period.over)}'s over — {_lower(text)}"


def _no_forecast(label: str) -> str:
    return f"I don't have a forecast for {label} yet."


def _chance(p: int, kind: str) -> str:
    return f"There's {a_or_an(p)} {p} percent chance of {kind}."


# --- Handlers --------------------------------------------------------------------------

WeatherAnswer = Callable[[Match, Forecast, datetime], str]


def weather_view(forecast: Forecast, now: datetime, place: str | None = None) -> WeatherView:
    return WeatherView(
        current=forecast.current,
        hourly=forecast.hourly_after(now, VIEW_HOURS),
        daily=forecast.daily_from(now.date(), VIEW_DAYS),
        place=place,
    )


def not_found(place: str) -> str:
    return f"Sorry, I couldn't find a place called {place.title()}."


def place_intro(pick: Pick) -> str:
    if pick.ambiguous:
        return f"There's more than one {pick.place.name}, so here's {pick.place.label}. "
    return f"Here's {pick.place.label}. "


def _handler(say: WeatherAnswer) -> Callable[..., object]:
    """Wrap a ``(match, forecast, now) -> say`` function: fetch weather for home or for the
    named place, attach the view."""

    async def at_place(
        place: str, match: Match, ctx: IntentContext, service: WeatherService
    ) -> Reply:
        try:
            pick = await service.resolve(place)
            if pick is None:
                return answer(not_found(place))
            snapshot = await service.get_at(pick.place)
        except WeatherFetchError:
            return answer(UNREACHABLE)
        now = ctx.clock().astimezone(ZoneInfo(pick.place.timezone))
        text = place_intro(pick) + say(match, snapshot.forecast, now)
        return Reply(say=text, view=weather_view(snapshot.forecast, now, pick.place.label))

    async def handle(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
        service: WeatherService | None = ctx.services.get("weather")
        place = match.slots.get("place", "").strip()
        if place and service is not None:
            return await at_place(place, match, ctx, service)
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
        sentences = [_say_day(forecast, day, day_label(d, now.date()), tz) for d, day in days]
        if period.lead is not None and (lead := _say_part(forecast, period.lead, now)):
            sentences.insert(0, lead)
        return " ".join(sentences)
    text = _say_part(forecast, period, now)
    return _over(period, text) if text else _no_forecast(period.label)


def _say_part(forecast: Forecast, period: Period, now: datetime) -> str | None:
    """Forecast for part of a day, or None without hourly data for it."""
    hours = _period_hours(forecast, period, now)
    code = representative_code(hours)
    if code is None:
        return None
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
        return _over(period, f"{text}. About {a_or_an(p)} {p} percent chance.")
    if p >= POSSIBLE_PERCENT:
        return _over(
            period, f"Maybe. There's {a_or_an(p)} {p} percent chance of {kind} {period.label}."
        )
    return _over(period, f"No {asked} expected {period.label}.")


_HIGH_WORDS = {"high", "warm", "hot"}
_LOW_WORDS = {"low", "cold"}


def say_high_low(match: Match, forecast: Forecast, now: datetime) -> str:
    which = match.slots.get("which", "high")
    want_high = which in _HIGH_WORDS or which == "high and low"
    want_low = which in _LOW_WORDS or which == "high and low"
    period = resolve_period(match.slots.get("when"), now)

    ranges: list[tuple[str, float, float]] = []  # (label, high, low)
    if period.lead is not None and (lead_hours := _period_hours(forecast, period.lead, now)):
        temps = [h.temp for h in lead_hours]
        ranges.append((period.lead.label, max(temps), min(temps)))
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
    return _over(period, " ".join(sentences))


# --- Patterns --------------------------------------------------------------------------

_OUT = r"(?: (?:outside|out there|out))?"
_NOW = r"(?: (?:right )?now)?"
_FEEL = r"(?:cold|warm|hot|chilly|freezing|nice|cool)"
_WILL = r"(?:will it|is it going to|is it gonna|it is going to)"
_PRECIP = r"(?:rain|snow|pour|drizzle|be raining|be snowing)"
_WHICH = r"(?P<which>high and low|high|low)"
_WEAR = (
    r"(?:do i|will i|should i|do we|will we|should we|does he|does she) "
    r"(?:need|wear|bring|take|put on) (?:a |an |my |our |some )?"
    r"(?:coat|jacket|winter coat|warm coat|sweater|jumper|hoodie|hat|toque|gloves|mittens|"
    r"scarf|boots|shorts|sunscreen|sun screen|sunhat|sun hat)"
)

CURRENT_PATTERNS = [
    rf"(?:what is |how is )?(?:the )?(?:current )?weather(?: like)?{_OUT}{_NOW}",
    rf"how is it looking{_OUT}{_NOW}",
    rf"how does it look{_OUT}{_NOW}",
    rf"what is it like{_OUT}{_NOW}",
    rf"is it {_FEEL}{_OUT}{_NOW}",
    rf"(?:what is )?(?:the )?(?:current )?(?:temperature|temp){_OUT}{_NOW}",
    rf"how {_FEEL} is it{_OUT}{_NOW}",
    # "is it warm enough for shorts", "is it cold enough for a hat": answered with conditions now
    rf"is it {_FEEL} enough(?: (?:for|to) [a-z0-9 ]+?)?{_OUT}{_NOW}",
    rf"{_WEAR}{_OUT}{_NOW}",
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
    rf"{_WEAR} {WHEN}",
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
    rf"how (?P<which>cold|warm|hot) is it (?:supposed|meant|expected|going) to (?:get|be)"
    rf"(?: {WHEN})?",
]


# --- Places ------------------------------------------------------------------------------

# "in <place>", optionally followed by when / now / outside: "what's the weather in paris
# tomorrow", "is it cold in paris right now". A place is up to four words of letters.
_PLACE = r"(?P<place>[a-z][a-z'.-]*(?: [a-z][a-z'.-]*){0,3}?)"
_AFTER_PLACE = re.compile(rf"{_PLACE}(?P<rest>(?: {WHEN.replace('?P<when>', '?:')})?{_OUT}{_NOW})")
# Words after "in" that are times or rooms, not places ("in the afternoon", "in here").
_NOT_PLACES = frozenset(
    "a an the here there this that my our your hour hours minute minutes second seconds "
    "morning afternoon evening night day days week weeks forecast future while bit house "
    "home kitchen garden yard backyard car shade sun".split()
)


def _is_place(text: str) -> bool:
    words = text.split()
    return words[0] not in _NOT_PLACES and words[-1] not in _NOT_PLACES


def _place_markers(text: str) -> list[int]:
    """Where " in <place>" could start, and " for <place>" right after "weather" or
    "forecast" ("the forecast for portland"; not "a coat for school")."""
    found = [m.start() for m in re.finditer(r"(?= in )", text)]
    found += [m.start() for m in re.finditer(r"(?:(?<=weather)|(?<=forecast))(?= for )", text)]
    return sorted(found)


def with_place(pattern: str) -> Callable[[str], Mapping[str, str] | None]:
    """A matcher for ``pattern`` with "in <place>" inserted after any prefix of it (and
    before an optional when / now): the place goes into the ``place`` slot."""
    compiled = re.compile(pattern)

    def match(text: str) -> Mapping[str, str] | None:
        for at in _place_markers(text):
            tail = _AFTER_PLACE.fullmatch(text[at:].split(" ", 2)[2])
            if tail is None or not _is_place(tail["place"]):
                continue
            m = compiled.fullmatch(text[:at] + tail["rest"])
            if m is not None:
                slots = {k: v for k, v in m.groupdict().items() if v is not None}
                return slots | {"place": tail["place"]}
        return None

    match.__name__ = f"with_place({pattern!r})"
    return match


def _patterns(patterns: list[str]) -> list[str | Callable[[str], Mapping[str, str] | None]]:
    return [*patterns, *(with_place(p) for p in patterns)]


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
LLM_PLACE = LlmSlot(
    hint=(
        "place is a city or town the person named, with its state, province or country if "
        'they said it ("Portland, Oregon"); leave it out for home.'
    ),
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
            patterns=_patterns(CURRENT_PATTERNS),
            handler=_handler(say_current),
            description=(
                "weather or temperature outside right now, including how cold, chilly or warm "
                "it is now"
            ),
            llm_slots={"place": LLM_PLACE},
        )
    )
    registry.register(
        Intent(
            "weather.forecast",
            patterns=_patterns(FORECAST_PATTERNS),
            handler=_handler(say_forecast),
            description=(
                "what the weather will be like later (today, tonight, tomorrow, the weekend), "
                "including what to wear"
            ),
            llm_slots={"when": LLM_WHEN, "place": LLM_PLACE},
        )
    )
    registry.register(
        Intent(
            "weather.rain",
            patterns=_patterns(RAIN_PATTERNS),
            handler=_handler(say_rain),
            description="only questions about rain, snow, showers, storms or umbrellas",
            llm_slots={"when": LLM_WHEN, "place": LLM_PLACE},
        )
    )
    registry.register(
        Intent(
            "weather.high_low",
            patterns=_patterns(HIGH_LOW_PATTERNS),
            handler=_handler(say_high_low),
            description=(
                "the forecast high or low temperature for a day or part of a day "
                "(how hot or how cold it will get), not the temperature right now"
            ),
            llm_slots={"when": LLM_WHEN, "which": LLM_WHICH, "place": LLM_PLACE},
        )
    )
