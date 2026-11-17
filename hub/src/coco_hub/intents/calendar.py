"""Calendar intents (#141): "what's on tomorrow" (``calendar.day``) and "when is soccer
practice" (``calendar.find``).

Both read the ``calendar`` service's cache (never the network) and answer with a spoken
summary (``calendar.spoken``) and the ``calendar`` view. No calendars configured: "Calendars
aren't set up yet." and no view.

Routing: day questions need a calendar word ("on", "going on", "happening", "planned", "the
calendar") and a day, so "what is today" stays the date and "how does tomorrow look" /
"what's today looking like" stay weather. "When is X" leaves timers, alarms and "it" alone
("when is the timer done", "when is it going to rain").

Answers contain event titles: nothing here logs them; the utterance trace is governed by
``diagnostics.log_transcripts``.
"""

from datetime import date, timedelta

from coco_hub import protocol as p
from coco_hub.calendar.days import protocol_days
from coco_hub.calendar.service import CalendarService, CalendarView
from coco_hub.calendar.spoken import (
    NOT_SET_UP,
    as_of,
    clean_query,
    day_summary,
    find_upcoming,
    match_days,
    not_found,
    when_is,
    with_prefix,
)
from coco_hub.intents.engine import Intent, IntentContext, IntentRegistry, LlmSlot, Match, Reply
from coco_hub.protocol import UtteranceRequest

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = "|".join(WEEKDAYS)

# --- calendar.day patterns ---------------------------------------------------------------

_DAY = rf"(?P<day>today|tomorrow|(?:this |the )?weekend|(?:this )?(?:{_WEEKDAY}))"
_CAL = r"(?:the |my |our |the family )?(?:calendar|schedule|agenda)"
_ON = r"(?:going on|happening|on|planned)"
_FOR = r"(?: for| on)?"
_ON_CAL = rf"(?: on {_CAL})?"
DAY_PATTERNS = [
    # "what's going on today", "what's on saturday", "what's on the calendar this weekend"
    rf"what is {_ON}{_ON_CAL}{_FOR} {_DAY}",
    # "what's on the calendar", "what's the schedule for tomorrow"
    rf"what is (?:on )?{_CAL}(?: like)?(?:{_FOR} {_DAY})?",
    # "anything on friday", "is there anything on the calendar for thursday"
    rf"(?:is there |do (?:we|i) have |have (?:we|i) got )?anything"
    rf"(?: {_ON})?{_ON_CAL}{_FOR} {_DAY}",
    # "what do we have going on tomorrow", "what have we got on sunday"
    rf"what (?:do|have) (?:we|i) (?:got |get |have )?{_ON}{_ON_CAL}{_FOR} {_DAY}",
    rf"what are (?:we|you) (?:doing|up to){_FOR} {_DAY}",
    rf"(?:are we|am i) (?:busy|free){_FOR} {_DAY}",
    rf"what (?:is the plan|are the plans|is our plan|are our plans){_FOR} {_DAY}",
    # "what's on today's calendar", "tomorrow's schedule"
    rf"(?:what is (?:on )?)?(?P<day>today|tomorrow|{_WEEKDAY})s"
    r" (?:calendar|schedule|agenda|plans?)",
]

# --- calendar.find patterns --------------------------------------------------------------

# Not an event: timers, alarms, "it" / "that", or another day ("when is it going to rain").
_NOT_EVENT = (
    r"(?!.*\b(?:timers?|alarms?)\b)"
    r"(?!(?:(?:the|my|our) )?(?:it|that|this|today|tomorrow|tonight|sunset|sunrise|to)\b)"
)
# ... nor something cooking: "when is the pasta ready".
_NOT_DONE = r"(?<! done)(?<! ready)(?<! finished)(?<! over)(?<! off)"
_QUERY = rf"{_NOT_EVENT}(?P<query>.+?){_NOT_DONE}(?: on the calendar| again)?"
FIND_PATTERNS = [
    rf"when is {_QUERY}",
    rf"when (?:do|does|will) (?:we|i|you|they|he|she) have {_QUERY}",
    rf"what day is {_QUERY}",
]


def resolve_day(day: str, today: date) -> tuple[str, str, list[date]]:
    """``day`` slot -> (view title, how to say it, the dates). A weekday is the next such day
    (today if it is today); the weekend is Saturday and Sunday, or just today on a Sunday."""
    words = day.lower().replace("_", " ").split()
    while words and words[0] in ("this", "the", "on", "next"):
        words = words[1:]
    word = " ".join(words) or "today"
    tomorrow = today + timedelta(days=1)
    if word == "tomorrow":
        return "Tomorrow", "tomorrow", [tomorrow]
    if word == "weekend":
        if today.weekday() == 6:
            return "This weekend", "this weekend", [today]
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        return "This weekend", "this weekend", [saturday, saturday + timedelta(days=1)]
    if word in WEEKDAYS:
        d = today + timedelta(days=(WEEKDAYS.index(word) - today.weekday()) % 7)
        if d == tomorrow:
            return "Tomorrow", "tomorrow", [tomorrow]
        if d != today:
            return f"{d:%A}", f"on {d:%A}", [d]
    return "Today", "today", [today]


def _cached(ctx: IntentContext) -> tuple[CalendarService, CalendarView] | None:
    service: CalendarService | None = ctx.services.get("calendar")
    view = service.view() if service is not None else None
    return None if service is None or view is None else (service, view)


def calendar_day(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    cached = _cached(ctx)
    if cached is None:
        return Reply(say=NOT_SET_UP)
    service, view = cached
    tz = service.timezone
    now = ctx.clock().astimezone(tz)
    title, period, days = resolve_day(match.slots.get("day", "today"), now.date())
    say = with_prefix(as_of(view, now), day_summary(view, days, period, now, tz))
    return Reply(
        say=say,
        view=p.CalendarView(
            title=title,
            query=None,
            days=protocol_days(view, days, tz),
            stale=view.stale,
            fetched_at=view.fetched_at,
        ),
    )


def calendar_find(match: Match, request: UtteranceRequest, ctx: IntentContext) -> Reply:
    cached = _cached(ctx)
    if cached is None:
        return Reply(say=NOT_SET_UP)
    service, view = cached
    tz = service.timezone
    now = ctx.clock().astimezone(tz)
    said = match.slots.get("query", "").strip()
    query = clean_query(said)
    found = find_upcoming(view, query, now) if query else []
    if found:
        sentence = when_is(found[0], now, tz)
        title = found[0].title
    else:
        sentence = not_found(said or "that")
        title = query[:1].upper() + query[1:]
    return Reply(
        say=with_prefix(as_of(view, now), sentence),
        view=p.CalendarView(
            title=title,
            query=query,
            days=protocol_days(view, match_days(found, now, tz), tz),
            stale=view.stale,
            fetched_at=view.fetched_at,
        ),
    )


LLM_DAY_VALUES = ("today", "tomorrow", "this_weekend", *WEEKDAYS)


def register(registry: IntentRegistry) -> None:
    registry.register(
        Intent(
            "calendar.day",
            patterns=DAY_PATTERNS,
            handler=calendar_day,
            description=(
                "what is on the family calendar for a day (events, plans, appointments; "
                "today, tomorrow, a weekday or this weekend), not the weather or the date"
            ),
            llm_slots={
                "day": LlmSlot(
                    enum=LLM_DAY_VALUES,
                    to_slot=lambda v: v.replace("_", " "),
                    hint=(
                        f"day is one of: {', '.join(LLM_DAY_VALUES)}; a weekday means the next "
                        "one; leave it out for today."
                    ),
                )
            },
        )
    )
    registry.register(
        Intent(
            "calendar.find",
            patterns=FIND_PATTERNS,
            handler=calendar_find,
            description=(
                'when an event on the family calendar is ("when is soccer practice", '
                '"when\'s the dentist")'
            ),
            llm_slots={
                "query": LlmSlot(
                    required=True,
                    hint=(
                        "for calendar.find, query is the event to look for, as said, without "
                        '"the" or "next": "when\'s the next soccer practice" -> "soccer practice".'
                    ),
                )
            },
        )
    )
