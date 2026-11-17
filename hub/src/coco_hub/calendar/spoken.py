"""What Coco says about the calendar (#141). Everything here reads a ``CalendarView`` (the
cache) and the Hub-local "now"; nothing fetches and nothing logs (answers carry event titles).

A day (or the weekend):

- count first, then every all-day item, then the first ``MAX_TIMED`` timed events, then
  "and N more": "3 things today: School holiday all day, Standup at 9:30 AM, and Soccer
  practice at 6 PM." One item: "1 thing tomorrow: ...". Nothing: "Nothing on the calendar
  on Thursday."
- Events that have already ended are left out of what is said (and of the count); the view
  still shows the whole day. If every event of the period is over: "Nothing else on the
  calendar today."
- Busy-only events give times only: "Busy from 2 to 3 PM", "Busy all day".
- An event that started on an earlier day says when it ends: "Sleepover until 10 AM".
- For the weekend (two days) items say which day: "Call with Grandma on Saturday at 9 AM";
  an all-day event covering both days is "all weekend".

Finding an event: "Soccer practice is Tuesday at 6 PM." (today / tomorrow / a weekday within
the week / the full date beyond), "Camping trip is Friday, all day.", "Camping trip is from
Friday to Sunday.", or, while it is on, "Soccer practice is on now, until 7 PM."

Stale data starts with when it is from: "As of 2 PM, ...", "As of yesterday at 2 PM, ...",
"As of November 24th at 2 PM, ...".
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coco_hub.calendar.days import day_bounds, on_day
from coco_hub.calendar.model import CalendarEvent
from coco_hub.calendar.service import CalendarView
from coco_hub.intents.builtin import ordinal
from coco_hub.weather.spoken import spoken_hour

MAX_TIMED = 3
NOT_SET_UP = "Calendars aren't set up yet."
NOT_ALL_LOADED = "Not every calendar has loaded yet. "


# --- words for times and days ------------------------------------------------------------


def clock_time(dt: datetime) -> str:
    """``6 PM``, ``9:30 AM``, ``noon``, ``12:30 PM``."""
    if dt.minute == 0:
        return spoken_hour(dt)
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def time_range(start: datetime, end: datetime) -> str:
    """``2 to 3 PM``, ``11 AM to 1 PM``, ``11 AM to noon``."""
    a, b = clock_time(start), clock_time(end)
    for suffix in (" AM", " PM"):
        if a.endswith(suffix) and b.endswith(suffix):
            return f"{a.removesuffix(suffix)} to {b}"
    return f"{a} to {b}"


def day_word(d: date, today: date) -> str:
    """``today``, ``tomorrow``, ``Saturday`` (within the week), ``Saturday, December 5th``."""
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if 1 < days < 7:
        return f"{d:%A}"
    return f"{d:%A}, {d:%B} {ordinal(d.day)}"


def on_day_word(d: date, today: date) -> str:
    """``today``, ``tomorrow``, ``on Saturday``."""
    word = day_word(d, today)
    return word if word in ("today", "tomorrow") else f"on {word}"


def joined(items: list[str]) -> str:
    """``A``, ``A, and B``, ``A, B, and C`` (always the serial comma, as in the protocol
    fixture)."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz)


def first_day(event: CalendarEvent, tz: ZoneInfo) -> date:
    return _local(event.start, tz).date()


def last_day(event: CalendarEvent, tz: ZoneInfo) -> date:
    """The last day ``event`` is on (ends are exclusive: an all-day event ends at midnight)."""
    return _local(event.end - timedelta(microseconds=1), tz).date()


# --- staleness ---------------------------------------------------------------------------


def as_of(view: CalendarView, now: datetime) -> str:
    """The prefix for stale data ("As of 2 PM, "), or "" when it is fresh."""
    if not view.stale:
        return ""
    if view.fetched_at is None:
        return NOT_ALL_LOADED
    fetched = view.fetched_at.astimezone(now.tzinfo)
    at = clock_time(fetched)
    if fetched.date() == now.date():
        return f"As of {at}, "
    if fetched.date() == now.date() - timedelta(days=1):
        return f"As of yesterday at {at}, "
    return f"As of {fetched:%B} {ordinal(fetched.day)} at {at}, "


def with_prefix(prefix: str, sentence: str) -> str:
    if prefix.endswith(", ") and sentence.startswith("Nothing"):
        sentence = "n" + sentence[1:]
    return prefix + sentence


# --- a day / the weekend -----------------------------------------------------------------


def _item(event: CalendarEvent, days: list[date], today: date, tz: ZoneInfo) -> str:
    covered = [d for d in days if first_day(event, tz) <= d <= last_day(event, tz)]
    single = len(days) == 1
    where = "" if single else f" {on_day_word(covered[0], today)}"
    if event.all_day:
        if single:
            return f"{event.title} all day"
        if len(covered) == len(days):
            return f"{event.title} all weekend"  # the weekend is the only multi-day period
        return f"{event.title}{where}"
    start, end = _local(event.start, tz), _local(event.end, tz)
    if event.start < day_bounds(covered[0], tz)[0]:
        return f"{event.title} until {clock_time(end)}{where}"
    if event.busy_only:
        return f"{event.title}{where} from {time_range(start, end)}"
    return f"{event.title}{where} at {clock_time(start)}"


def day_summary(
    view: CalendarView, days: list[date], period: str, now: datetime, tz: ZoneInfo
) -> str:
    """What is on ``days`` (one day, or Saturday and Sunday). ``period`` is how to say them:
    ``today``, ``tomorrow``, ``on Saturday``, ``this weekend``."""
    events: dict[tuple[str, str], CalendarEvent] = {}
    for d in days:
        for e in on_day(view, d, tz):
            events.setdefault((e.calendar.name, e.id), e)
    if not events:
        return f"Nothing on the calendar {period}."
    left = [e for e in events.values() if e.end > now]
    if not left:
        return f"Nothing else on the calendar {period}."
    all_day = sorted((e for e in left if e.all_day), key=lambda e: (e.start, e.title))
    timed = sorted((e for e in left if not e.all_day), key=lambda e: (e.start, e.title))
    today = now.date()
    items = [_item(e, days, today, tz) for e in [*all_day, *timed[:MAX_TIMED]]]
    if len(timed) > MAX_TIMED:
        items.append(f"{len(timed) - MAX_TIMED} more")
    count = "1 thing" if len(left) == 1 else f"{len(left)} things"
    return f"{count} {period}: {joined(items)}."


# --- finding an event --------------------------------------------------------------------

_DETERMINERS = ("the", "my", "our", "your", "next", "a", "an")
_IGNORED = frozenset({*_DETERMINERS, "with", "and", "of", "for", "to", "at", "on", "in", "s"})


def clean_query(said: str) -> str:
    """The query without leading "the" / "my" / "next": "the next soccer practice" ->
    "soccer practice"."""
    words = said.lower().split()
    while words and words[0] in _DETERMINERS:
        words = words[1:]
    return " ".join(words)


def _stem(word: str) -> str:
    """Plurals and possessives ("lessons", "grandma's" -> "grandmas") match their stem."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())} - _IGNORED


def title_matches(query: str, title: str) -> bool:
    """Every word of the query is in the title, ignoring case, punctuation, plurals,
    possessives and little words: "grandmas call" matches "Call with Grandma"."""
    wanted = _words(query)
    return bool(wanted) and wanted <= _words(title)


def find_upcoming(view: CalendarView, query: str, now: datetime) -> list[CalendarEvent]:
    """Events from now to the end of the cache whose title matches, soonest first. The event
    in progress counts; busy-only events never match (their title is hidden)."""
    found = [
        e for e in view.events if not e.busy_only and e.end > now and title_matches(query, e.title)
    ]
    return sorted(found, key=lambda e: (e.start, e.title))


def match_days(events: list[CalendarEvent], now: datetime, tz: ZoneInfo) -> list[date]:
    """Every day (from today) that the found events are on."""
    days: set[date] = set()
    for e in events:
        d = max(first_day(e, tz), now.date())
        while d <= last_day(e, tz):
            days.add(d)
            d += timedelta(days=1)
    return sorted(days)


def when_is(event: CalendarEvent, now: datetime, tz: ZoneInfo) -> str:
    today = now.date()
    title = event.title
    if event.all_day:
        first, last = max(first_day(event, tz), today), last_day(event, tz)
        if first == last:
            return f"{title} is {day_word(first, today)}, all day."
        return f"{title} is from {day_word(first, today)} to {day_word(last, today)}."
    start, end = _local(event.start, tz), _local(event.end, tz)
    if event.start <= now:
        until = clock_time(end)
        if end.date() != today:
            until = f"{day_word(end.date(), today)} at {until}"
        return f"{title} is on now, until {until}."
    return f"{title} is {day_word(start.date(), today)} at {clock_time(start)}."


def not_found(said: str) -> str:
    return f"I don't see {said} on the calendar."
