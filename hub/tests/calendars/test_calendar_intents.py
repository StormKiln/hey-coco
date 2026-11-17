"""Calendar voice intents (#141): "what's on tomorrow", "when is soccer practice".

Fictional events only. The Hub is in America/Chicago; "now" is Tuesday 2026-11-24.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.calendar.model import CalendarEvent, CalendarRef, FetchResult, Unreachable
from coco_hub.calendar.service import CalendarService
from coco_hub.intents import INTENT_MODULES, build_registry
from coco_hub.intents import calendar as calendar_intents
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import CalendarView, UtteranceContext, UtteranceRequest
from coco_hub.settings import Settings

from conftest import TEST_TOKEN

pytestmark = pytest.mark.anyio

TZ = ZoneInfo("America/Chicago")
DAY, FIND = "calendar.day", "calendar.find"
EVAL = Path(__file__).resolve().parents[2] / "eval" / "utterances.jsonl"
FAMILY = "https://family.example.invalid/family.ics"
WORK = "https://work.example.invalid/work.ics"


def at(d: int, h: int = 0, mi: int = 0, mo: int = 11) -> datetime:
    return datetime(2026, mo, d, h, mi, tzinfo=TZ)


def ev(title: str, start: datetime, hours: float = 1, *, days: int = 0) -> CalendarEvent:
    """A timed event, or with ``days`` an all-day event of that many days."""
    end = start + (timedelta(days=days) if days else timedelta(hours=hours))
    return CalendarEvent(
        id=f"{title}-{start.date()}@example.invalid",
        title=title,
        start=start,
        end=end,
        all_day=bool(days),
        calendar=CalendarRef(name=""),
    )


FAMILY_EVENTS = [
    # Tuesday 24 (today)
    ev("Breakfast with neighbours", at(24, 7)),
    ev("School holiday", at(24), days=1),
    ev("Standup", at(24, 9, 30), 0.25),
    ev("Soccer practice", at(24, 18)),
    # Friday 27 .. Sunday 29
    ev("Camping trip", at(27), days=3),
    # Saturday 28
    ev("Call with Grandma", at(28, 9)),
    ev("Swim lessons", at(28, 10)),
    ev("Birthday party", at(28, 13)),
    ev("Pizza night", at(28, 18)),
    ev("Sleepover", at(28, 19), 15),
    # next weeks
    ev("Soccer practice", at(1, 18, mo=12)),
    ev("Craft fair", at(5, 10, mo=12), 3),
    ev("Soccer practice", at(8, 18, 30, mo=12)),
]
WORK_EVENTS = [  # a busy_only calendar: titles become "Busy"
    ev("Dentist", at(25, 14)),
    ev("Conference", at(2, mo=12), days=1),
]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeSource:
    def __init__(self, name: str, events: list[CalendarEvent]) -> None:
        self.name = name
        self.events = events
        self.error: Exception | None = None

    async def fetch(self, start: datetime, end: datetime) -> FetchResult:
        if self.error is not None:
            raise self.error
        return FetchResult(
            name=self.name, events=[e for e in self.events if e.end > start and e.start < end]
        )

    async def aclose(self) -> None:
        pass


def hub_settings(tmp_path, calendars) -> Settings:
    return Settings(
        token=TEST_TOKEN,
        data_dir=tmp_path / "data",
        discovery={"enabled": False},
        timezone="America/Chicago",
        calendars=calendars,
    )


@pytest.fixture
def clock() -> Clock:
    return Clock(at(24, 9))


@pytest.fixture
def feeds() -> dict[str, FakeSource]:
    return {FAMILY: FakeSource("Family", FAMILY_EVENTS), WORK: FakeSource("Work", WORK_EVENTS)}


@pytest.fixture
def settings_with_calendars(tmp_path) -> Settings:
    return hub_settings(
        tmp_path,
        [{"source": "ics", "url": FAMILY}, {"source": "ics", "url": WORK, "busy_only": True}],
    )


@pytest.fixture
async def service(settings_with_calendars, clock, feeds):
    svc = CalendarService(
        settings_with_calendars,
        clock=clock,
        source_factory=lambda config, tz: feeds[config.options.url.get_secret_value()],
    )
    await svc.refresh()
    return svc


def request(text: str) -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        source="voice",
        context=UtteranceContext(view="idle", alarm_ringing=False, media_playing=None),
    )


@pytest.fixture
def ask(service, settings_with_calendars, clock):
    registry = build_registry(include_devices=False)

    async def ask(text: str, *, services=None, settings=None, fresh=True):
        """``fresh``: refresh the cache at the clock's time first (else it may be stale)."""
        if fresh and services is None:
            await service.refresh()
        ctx = IntentContext(
            settings=settings or settings_with_calendars,
            clock=clock,
            services={"calendar": service} if services is None else services,
        )
        return await RulesEngine(registry).handle(request(text), ctx)

    return ask


# --- routing -----------------------------------------------------------------------------

PHRASES = [
    ("what's going on today", DAY, {"day": "today"}),
    ("what's on tomorrow", DAY, {"day": "tomorrow"}),
    ("what's on the calendar this weekend", DAY, {"day": "this weekend"}),
    ("what's on Saturday", DAY, {"day": "saturday"}),
    ("anything on Friday", DAY, {"day": "friday"}),
    ("what do we have going on tomorrow", DAY, {"day": "tomorrow"}),
    ("Hey Coco, what's happening this Saturday?", DAY, {"day": "this saturday"}),
    ("is there anything on the calendar for Thursday", DAY, {"day": "thursday"}),
    ("do we have anything on this weekend", DAY, {"day": "this weekend"}),
    ("what have we got on Sunday", DAY, {"day": "sunday"}),
    ("what's on the schedule for tomorrow", DAY, {"day": "tomorrow"}),
    ("what's on the calendar", DAY, {}),
    ("what's on today's calendar", DAY, {"day": "today"}),
    ("what are we doing this weekend", DAY, {"day": "this weekend"}),
    ("when is soccer practice", FIND, {"query": "soccer practice"}),
    ("when's the dentist", FIND, {"query": "the dentist"}),
    ("when is grandma's call", FIND, {"query": "grandmas call"}),
    ("When is the next soccer practice?", FIND, {"query": "the next soccer practice"}),
    ("when do we have swim lessons", FIND, {"query": "swim lessons"}),
    ("when is the craft fair on the calendar", FIND, {"query": "the craft fair"}),
    # Not calendar questions: other intents keep them.
    ("what is today", "date.today", {}),
    ("what's today's date", "date.today", {}),
    ("what day is it", "date.today", {}),
    ("what time is it", "time.now", {}),
    ("how does tomorrow look", "weather.forecast", {"when": "tomorrow"}),
    ("what's today looking like", "weather.forecast", {"when": "today"}),
    ("what's the weather tomorrow", "weather.forecast", {"when": "tomorrow"}),
    ("what's the weather like this weekend", "weather.forecast", {"when": "this weekend"}),
]


@pytest.mark.parametrize(("phrase", "intent", "slots"), PHRASES)
def test_phrase_to_intent(phrase, intent, slots):
    match = build_registry(include_devices=False).match(normalize(phrase))
    assert match is not None, phrase
    assert (match.intent, match.slots) == (intent, slots)


@pytest.mark.parametrize(
    "phrase",
    [
        "when is the timer done",
        "when is my pizza timer going to go off",
        "when is it going to rain",
        "when is the pasta ready",
        "what's going on",
    ],
)
def test_not_calendar(phrase):
    match = build_registry(include_devices=False).match(normalize(phrase))
    assert match is None or not match.intent.startswith("calendar."), phrase


def test_calendar_module_is_registered():
    assert calendar_intents.register in INTENT_MODULES
    assert {DAY, FIND} <= {i.name for i in build_registry()}


def test_eval_utterances_route_as_before_calendar_intents():
    """Adding calendar intents changes no eval line's rules match except calendar ones."""
    with_calendar = build_registry(include_devices=False)
    without = IntentRegistry()
    for register in INTENT_MODULES:
        if register is not calendar_intents.register:
            register(without)
    for line in EVAL.read_text().splitlines():
        case = json.loads(line)
        ctx = UtteranceContext(
            view="idle",
            alarm_ringing=bool(case.get("context", {}).get("alarm_ringing")),
            media_playing=case.get("context", {}).get("media_playing"),
        )
        text = normalize(case["text"])
        before, after = without.match(text, ctx), with_calendar.match(text, ctx)
        if case["intent"].startswith("calendar."):  # rules may leave some to Qwen
            assert after is None or after.intent == case["intent"], case["text"]
        elif before is None:
            assert after is None or not after.intent.startswith("calendar."), case["text"]
        else:
            assert after is not None and after.intent == before.intent, case["text"]


def test_eval_has_calendar_and_regression_cases():
    cases = [json.loads(line) for line in EVAL.read_text().splitlines()]
    texts = {c["text"].lower(): c["intent"] for c in cases}
    assert texts["what's going on today"] == DAY
    assert texts["when is soccer practice"] == FIND
    assert texts["what is today"] == "date.today"
    assert texts["how does tomorrow look"] == "weather.forecast"


# --- calendar.day ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "say"),
    [
        (
            "what's going on today",
            "3 things today: School holiday all day, Standup at 9:30 AM, "
            "and Soccer practice at 6 PM.",
        ),
        ("what's on tomorrow", "1 thing tomorrow: Busy from 2 to 3 PM."),
        ("what's on Thursday", "Nothing on the calendar on Thursday."),
        ("anything on Friday", "1 thing on Friday: Camping trip all day."),
        (
            "what's on Saturday",
            "6 things on Saturday: Camping trip all day, Call with Grandma at 9 AM, "
            "Swim lessons at 10 AM, Birthday party at 1 PM, and 2 more.",
        ),
        (
            "what's on the calendar this weekend",
            "6 things this weekend: Camping trip all weekend, "
            "Call with Grandma on Saturday at 9 AM, Swim lessons on Saturday at 10 AM, "
            "Birthday party on Saturday at 1 PM, and 2 more.",
        ),
        (
            "what's on Tuesday",
            "3 things today: School holiday all day, Standup at 9:30 AM, "
            "and Soccer practice at 6 PM.",
        ),
        ("what's on Wednesday", "1 thing tomorrow: Busy from 2 to 3 PM."),
        ("what's on Wednesday next week", None),  # not a supported phrasing
    ],
)
async def test_day_answers(ask, text, say):
    action = await ask(text)
    if say is None:
        assert getattr(action, "intent", None) != DAY
        return
    assert action.intent == DAY
    assert action.say == say


async def test_day_view(ask):
    action = await ask("what's on Saturday")
    view = action.view
    assert isinstance(view, CalendarView)
    assert (view.title, view.query, view.stale) == ("Saturday", None, False)
    assert [d.date for d in view.days] == ["2026-11-28"]
    titles = [e.title for e in view.days[0].events]
    assert titles[0] == "Camping trip"  # all-day first
    assert "Sleepover" in titles


@pytest.mark.parametrize(
    ("text", "title", "dates"),
    [
        ("what's going on today", "Today", ["2026-11-24"]),
        ("what's on tomorrow", "Tomorrow", ["2026-11-25"]),
        ("what's on Wednesday", "Tomorrow", ["2026-11-25"]),
        ("what's on Monday", "Monday", ["2026-11-30"]),
        ("what's on this weekend", "This weekend", ["2026-11-28", "2026-11-29"]),
    ],
)
async def test_day_view_titles_and_dates(ask, text, title, dates):
    view = (await ask(text)).view
    assert view.title == title
    assert [d.date for d in view.days] == dates


async def test_today_leaves_out_events_that_have_ended_but_the_view_keeps_them(ask, clock):
    clock.now = at(24, 10)
    action = await ask("what's going on today")
    assert action.say == "2 things today: School holiday all day, and Soccer practice at 6 PM."
    titles = [e.title for e in action.view.days[0].events]
    assert "Breakfast with neighbours" in titles and "Standup" in titles


async def test_today_after_everything_has_ended(ask, clock):
    clock.now = at(24, 23, 30)
    action = await ask("what's going on today")
    # The all-day holiday runs until midnight.
    assert action.say == "1 thing today: School holiday all day."


async def test_nothing_else_today(service, ask, clock, feeds):
    feeds[FAMILY].events = [ev("Breakfast with neighbours", at(24, 7))]
    await service.refresh()
    clock.now = at(24, 9, 1)
    assert (await ask("what's going on today")).say == "Nothing else on the calendar today."


async def test_event_that_started_before_the_day(ask):
    action = await ask("what's on Sunday")
    assert action.say == "2 things on Sunday: Camping trip all day, and Sleepover until 10 AM."


async def test_weekend_on_sunday_is_just_today(ask, clock):
    clock.now = at(29, 11)
    action = await ask("what's on this weekend")
    assert action.say == "1 thing this weekend: Camping trip all day."
    assert action.view.title == "This weekend"
    assert [d.date for d in action.view.days] == ["2026-11-29"]


async def test_busy_all_day(ask, clock):
    clock.now = at(1, 9, mo=12)
    action = await ask("what's on tomorrow")
    assert action.say == "1 thing tomorrow: Busy all day."


async def test_stale_data_says_when_it_is_from(service, ask, clock):
    clock.now = at(24, 14)
    await service.refresh()
    clock.now = at(24, 15)  # no refresh for an hour
    action = await ask("what's on tomorrow", fresh=False)
    assert action.say == "As of 2 PM, 1 thing tomorrow: Busy from 2 to 3 PM."
    assert action.view.stale
    assert action.view.fetched_at == at(24, 14)


async def test_stale_nothing_is_lower_case(service, ask, clock):
    clock.now = at(24, 9, 52)
    await service.refresh()
    clock.now = at(24, 11)
    action = await ask("what's on Thursday", fresh=False)
    assert action.say == "As of 9:52 AM, nothing on the calendar on Thursday."


async def test_stale_from_another_day_names_the_day(service, ask, clock, feeds):
    feeds[FAMILY].error = Unreachable()
    clock.now = at(25, 8)
    await service.refresh()
    action = await ask("what's on Saturday", fresh=False)
    assert action.say.startswith("As of yesterday at 9 AM, 6 things on Saturday:")
    clock.now = at(27, 8)
    action = await ask("what's on Saturday", fresh=False)
    assert action.say.startswith("As of November 24th at 9 AM, 6 things tomorrow:")


async def test_no_calendars_configured(ask, tmp_path, clock):
    settings = hub_settings(tmp_path, [])
    empty = CalendarService(settings, clock=clock)
    for services in ({"calendar": empty}, {}):
        action = await ask("what's on tomorrow", services=services, settings=settings)
        assert action.say == "Calendars aren't set up yet."
        assert action.view is None
        action = await ask("when is soccer practice", services=services, settings=settings)
        assert action.say == "Calendars aren't set up yet."
        assert action.view is None


# --- calendar.find -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "text", "say"),
    [
        (at(24, 9), "when is soccer practice", "Soccer practice is today at 6 PM."),
        (at(29, 9), "when is soccer practice", "Soccer practice is Tuesday at 6 PM."),
        (at(30, 9), "when's the next soccer practice", "Soccer practice is tomorrow at 6 PM."),
        (at(24, 18, 15), "when is soccer practice", "Soccer practice is on now, until 7 PM."),
        (at(24, 9), "When is Grandma's call?", "Call with Grandma is Saturday at 9 AM."),
        (at(24, 9), "when do we have swim lesson", "Swim lessons is Saturday at 10 AM."),
        (at(24, 9), "when is the camping trip", "Camping trip is from Friday to Sunday."),
        (at(28, 9), "when is the camping trip", "Camping trip is from today to tomorrow."),
        (at(24, 9), "when is the school holiday", "School holiday is today, all day."),
        (at(24, 9), "when is the craft fair", "Craft fair is Saturday, December 5th at 10 AM."),
        (at(24, 9), "WHEN IS SOCCER PRACTICE!", "Soccer practice is today at 6 PM."),
        (at(24, 9), "when's the dentist", "I don't see the dentist on the calendar."),
        (at(24, 9), "when is the bake sale", "I don't see the bake sale on the calendar."),
        (at(24, 9), "when is breakfast", "I don't see breakfast on the calendar."),  # it's over
    ],
)
async def test_find_answers(ask, clock, now, text, say):
    clock.now = now
    action = await ask(text)
    assert action.intent == FIND
    assert action.say == say


async def test_busy_only_events_are_never_matched_by_title(ask):
    # The work calendar's "Dentist" is busy-only; "busy" isn't a title to search either.
    assert (await ask("when is the dentist")).say == "I don't see the dentist on the calendar."
    assert (await ask("when is busy")).say == "I don't see busy on the calendar."


async def test_find_view(ask):
    action = await ask("when is soccer practice")
    view = action.view
    assert isinstance(view, CalendarView)
    assert (view.title, view.query, view.stale) == ("Soccer practice", "soccer practice", False)
    assert [d.date for d in view.days] == ["2026-11-24", "2026-12-01", "2026-12-08"]


async def test_find_view_for_a_multi_day_event(ask):
    view = (await ask("when is the camping trip")).view
    assert view.query == "camping trip"
    assert [d.date for d in view.days] == ["2026-11-27", "2026-11-28", "2026-11-29"]


async def test_find_not_found_view(ask):
    action = await ask("when is the bake sale")
    view = action.view
    assert (view.title, view.query, view.days) == ("Bake sale", "bake sale", [])


async def test_find_stale(service, ask, clock):
    clock.now = at(24, 11)
    action = await ask("when is soccer practice", fresh=False)
    assert action.say == "As of 9 AM, Soccer practice is today at 6 PM."


async def test_find_through_qwen_slots(service, settings_with_calendars, clock):
    """Qwen fills ``query`` / ``day`` as typed values; the handlers read them the same way."""
    registry = build_registry(include_devices=False)
    engine = RulesEngine(registry)
    ctx = IntentContext(settings_with_calendars, clock=clock, services={"calendar": service})
    from coco_hub.intents.engine import Match

    found = registry.get(FIND).llm_slots["query"].handler_slot("query", "Soccer practice")
    action = await engine.dispatch(
        Match(FIND, dict([found]), ""), request("x"), ctx, matched_by="qwen"
    )
    assert action.say == "Soccer practice is today at 6 PM."
    day = registry.get(DAY).llm_slots["day"].handler_slot("day", "this_weekend")
    action = await engine.dispatch(Match(DAY, dict([day]), ""), request("x"), ctx, "qwen")
    assert action.view.title == "This weekend"
    action = await engine.dispatch(Match(DAY, {}, ""), request("x"), ctx, "qwen")
    assert action.view.title == "Today"


# --- privacy -----------------------------------------------------------------------------


ICS = (Path(__file__).resolve().parents[1] / "fixtures" / "calendar" / "family.ics").read_bytes()


def test_calendar_answers_stay_out_of_the_log_without_log_transcripts(tmp_path, caplog):
    """Spoken answers carry event titles: with ``log_transcripts = false`` none reaches a log."""
    settings = hub_settings(tmp_path, [{"source": "ics", "url": FAMILY}])
    settings = settings.model_copy(
        update={"diagnostics": settings.diagnostics.model_copy(update={"log_transcripts": False})}
    )
    fixed = datetime(2026, 11, 24, 16, 0, tzinfo=UTC)  # 10 AM in Chicago
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=ICS, headers={"Content-Type": "text/calendar"})
    )
    service = CalendarService(settings, clock=lambda: fixed, transport=transport)
    app = create_app(settings, clock=lambda: fixed, calendar=service)
    body = {
        "text": "what's going on today",
        "source": "voice",
        "context": {"view": "idle", "alarm_ringing": False, "media_playing": None},
    }
    with TestClient(app) as client, caplog.at_level(logging.DEBUG):
        client.portal.call(service.refresh)
        response = client.post(
            "/v1/utterance", json=body, headers={"Authorization": f"Bearer {TEST_TOKEN}"}
        )
    action = response.json()
    assert action["intent"] == DAY
    assert "Soccer practice" in action["say"]  # the answer does carry a title
    [trace] = [r for r in caplog.records if r.name == "coco_hub.utterance"]
    assert "say" not in trace.trace and "text" not in trace.trace
    logged = "\n".join(r.getMessage() + repr(getattr(r, "trace", "")) for r in caplog.records)
    assert "Soccer practice" not in logged
    assert "going on" not in logged
