from datetime import UTC, datetime, timedelta

import pytest

from coco_hub import db
from coco_hub.events import EventBus
from coco_hub.intents import builtin, timers
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, UtteranceContext, UtteranceRequest
from coco_hub.timers.service import TimerService

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
QUIET = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
RINGING = UtteranceContext(view="timers", alarm_ringing=True, media_playing=None)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    builtin.register(r)
    timers.register(r)
    return r


# --- phrase → intent (+ slots) -----------------------------------------------------------

M = 60
PHRASES: list[tuple[str, UtteranceContext, str | None, dict[str, str]]] = [
    # timer.create
    ("set a pasta timer for 10 minutes", QUIET, "timer.create", {"name": "pasta"}),
    ("Set a timer for ten minutes", QUIET, "timer.create", {}),
    ("10 minute timer", QUIET, "timer.create", {}),
    ("set a 10-minute timer", QUIET, "timer.create", {}),
    ("timer for an hour and a half", QUIET, "timer.create", {}),
    ("set a timer for 5 minutes called eggs", QUIET, "timer.create", {"name": "eggs"}),
    ("set a timer for 5 minutes named eggs", QUIET, "timer.create", {"name": "eggs"}),
    ("set a timer for 10 minutes for the rice", QUIET, "timer.create", {"name": "rice"}),
    ("remind me in 10 minutes", QUIET, "timer.create", {}),
    ("start a 3 minute egg timer", QUIET, "timer.create", {"name": "egg"}),
    ("hey coco start a timer for 90 seconds", QUIET, "timer.create", {}),
    ("set a timer for a quarter of an hour", QUIET, "timer.create", {}),
    ("a quarter of an hour on the lasagna", QUIET, "timer.create", {"name": "lasagna"}),
    ("can you set a timer for half an hour please", QUIET, "timer.create", {}),
    ("make a cookies timer for 12 minutes", QUIET, "timer.create", {"name": "cookies"}),
    ("set another timer for 2 minutes", QUIET, "timer.create", {}),
    ("timer 5 minutes", QUIET, "timer.create", {}),
    # timer.query
    ("how long on the pasta", QUIET, "timer.query", {"name": "pasta"}),
    ("how much time is left", QUIET, "timer.query", {}),
    ("how long left on the timer", QUIET, "timer.query", {}),
    ("how much time is left on the pasta timer", QUIET, "timer.query", {"name": "pasta"}),
    ("how long is left on the eggs", QUIET, "timer.query", {"name": "eggs"}),
    ("how much longer on the rice", QUIET, "timer.query", {"name": "rice"}),
    ("how long until the pasta is done", QUIET, "timer.query", {"name": "pasta"}),
    ("when will the pasta be done", QUIET, "timer.query", {"name": "pasta"}),
    ("time left", QUIET, "timer.query", {}),
    ("how is the pasta timer doing", QUIET, "timer.query", {"name": "pasta"}),
    # timer.list
    ("what timers do I have", QUIET, "timer.list", {}),
    ("what timers are running", QUIET, "timer.list", {}),
    ("list my timers", QUIET, "timer.list", {}),
    ("show me the timers", QUIET, "timer.list", {}),
    ("do I have any timers", QUIET, "timer.list", {}),
    ("how many timers are running", QUIET, "timer.list", {}),
    ("are there any timers set", QUIET, "timer.list", {}),
    # timer.cancel
    ("cancel the pasta timer", QUIET, "timer.cancel", {"name": "pasta"}),
    ("stop the timer", QUIET, "timer.cancel", {}),
    ("cancel the timer", QUIET, "timer.cancel", {}),
    ("cancel all timers", QUIET, "timer.cancel", {"all": "all"}),
    ("cancel all the timers", QUIET, "timer.cancel", {"all": "all"}),
    ("clear my timers", QUIET, "timer.cancel", {"all": "timers"}),
    ("delete the eggs timer", QUIET, "timer.cancel", {"name": "eggs"}),
    ("cancel the pasta", QUIET, "timer.cancel", {"name": "pasta"}),
    ("scrap the broccoli one", QUIET, "timer.cancel", {"name": "broccoli"}),
    ("turn off the rice timer", QUIET, "timer.cancel", {"name": "rice"}),
    ("cancel timer two", QUIET, "timer.cancel", {"name": "timer 2"}),
    ("cancel the pasta timer", RINGING, "timer.cancel", {"name": "pasta"}),
    # timer.add_time
    ("add 5 minutes to the pasta timer", QUIET, "timer.add_time", {"name": "pasta"}),
    ("add 2 more minutes", QUIET, "timer.add_time", {}),
    ("add another minute", QUIET, "timer.add_time", {}),
    ("add 30 seconds", QUIET, "timer.add_time", {}),
    ("add 5 minutes to the eggs", QUIET, "timer.add_time", {"name": "eggs"}),
    ("give the pasta 3 more minutes", QUIET, "timer.add_time", {"name": "pasta"}),
    ("put 5 more minutes on the rice", QUIET, "timer.add_time", {"name": "rice"}),
    ("5 more minutes", QUIET, "timer.add_time", {}),
    ("2 more minutes", RINGING, "timer.add_time", {}),
    # alarm.stop — only while ringing
    ("stop", RINGING, "alarm.stop", {}),
    ("ok", RINGING, "alarm.stop", {}),
    ("okay thanks", RINGING, "alarm.stop", {}),
    ("dismiss", RINGING, "alarm.stop", {}),
    ("shut up", RINGING, "alarm.stop", {}),
    ("thanks coco", RINGING, "alarm.stop", {}),
    ("thank you", RINGING, "alarm.stop", {}),
    ("stop the timer", RINGING, "alarm.stop", {}),
    ("stop the alarm", RINGING, "alarm.stop", {}),
    ("turn it off", RINGING, "alarm.stop", {}),
    ("I got it", RINGING, "alarm.stop", {}),
    ("Hey Coco, stop!", RINGING, "alarm.stop", {}),
    ("stop", QUIET, None, {}),
    ("ok", QUIET, None, {}),
    ("dismiss", QUIET, None, {}),
    ("shut up", QUIET, None, {}),
    ("thanks coco", QUIET, None, {}),
    # not timers
    ("stop the music", QUIET, None, {}),
    ("what time is it", QUIET, "time.now", {}),
    ("time", QUIET, "time.now", {}),
    ("what is the weather", QUIET, None, {}),
    ("10 minutes", QUIET, None, {}),
    ("set an alarm for 7", QUIET, None, {}),
]


@pytest.mark.parametrize(("phrase", "context", "intent", "slots"), PHRASES)
def test_phrase_to_intent(registry, phrase, context, intent, slots):
    match = registry.match(normalize(phrase), context)
    if intent is None:
        assert match is None or not match.intent.startswith(("timer.", "alarm.")), match
        return
    assert match is not None, f"{phrase!r} matched nothing"
    assert match.intent == intent
    for key, value in slots.items():
        assert match.slots.get(key) == value, match.slots


def test_enough_phrases():
    assert len(PHRASES) >= 40


# --- handlers ----------------------------------------------------------------------------


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(tmp_path, clock):
    conn = db.open_db(tmp_path)
    yield TimerService(conn, clock=clock, bus=EventBus())
    conn.close()


@pytest.fixture
def say(registry, service, settings, clock):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, clock=clock, services={"timers": service})

    async def run(text: str, context: UtteranceContext = QUIET) -> Action:
        result = await engine.handle(
            UtteranceRequest(text=text, source="voice", context=context), ctx
        )
        assert isinstance(result, Action), f"{text!r} did not match"
        return result

    return run


async def test_create_named(say, service):
    action = await say("set a pasta timer for 10 minutes")
    assert action.say == "Pasta timer set for 10 minutes."
    assert action.view is not None and action.view.type == "timers"
    [timer] = service.list_active()
    assert (timer.name, timer.duration_s) == ("pasta", 10 * M)


async def test_create_variants_table(say, service):
    cases = [
        ("10 minute timer", "Timer set for 10 minutes.", 600),
        ("timer for an hour and a half", "Timer 2 set for 1 hour and 30 minutes.", 5400),
        ("set a timer for a quarter of an hour", "Timer 3 set for 15 minutes.", 900),
        ("a quarter of an hour on the lasagna", "Lasagna timer set for 15 minutes.", 900),
        ("set a timer for 90 seconds", "Timer 4 set for 1 minute and 30 seconds.", 90),
        ("set a timer for 5 minutes called eggs", "Eggs timer set for 5 minutes.", 300),
    ]
    for phrase, expected, _ in cases:
        assert (await say(phrase)).say == expected, phrase
    assert sorted(t.duration_s for t in service.list_active()) == sorted(c[2] for c in cases)


async def test_create_rejects_too_long(say, service):
    action = await say("set a timer for 30 hours")
    assert action.say == "Timers can be up to 24 hours."
    assert service.list_active() == []


async def test_query_named_and_single(say, service, clock):
    service.create("pasta", 10 * M)
    clock.advance(minutes=6, seconds=10)
    assert (await say("how long on the pasta")).say == "About 4 minutes left on the pasta timer."
    assert (await say("how much time is left")).say == "About 4 minutes left on the pasta timer."
    clock.advance(minutes=3, seconds=20)
    assert (await say("how long left on the timer")).say == "30 seconds left on the pasta timer."


async def test_query_unknown_name(say, service):
    service.create("pasta", 10 * M)
    assert (await say("how long on the rice")).say == "You don't have a rice timer."


async def test_query_with_no_timers(say):
    assert (await say("how much time is left")).say == "You don't have any timers."


async def test_query_ambiguous_asks_which(say, service):
    service.create("pasta", 10 * M)
    service.create("eggs", 7 * M)
    assert (await say("how much time is left")).say == "Which timer? You have eggs and pasta."


async def test_query_ringing(say, service, clock):
    service.create("pasta", M)
    clock.advance(minutes=1)
    service.tick()
    assert (await say("how long on the pasta")).say == "The pasta timer is done."


async def test_list(say, service, clock):
    assert (await say("what timers do I have")).say == "You don't have any timers."
    service.create("pasta", 10 * M)
    assert (await say("what timers do I have")).say == ("You have 1 timer: pasta, 10 minutes left.")
    service.create("eggs", 30)
    assert (await say("what timers do I have")).say == (
        "You have 2 timers: eggs, 30 seconds left; and pasta, 10 minutes left."
    )


async def test_cancel_named(say, service):
    service.create("pasta", 10 * M)
    eggs = service.create("eggs", 10 * M)
    assert (await say("cancel the pasta timer")).say == "Pasta timer cancelled."
    assert [t.id for t in service.list_active()] == [eggs.id]


async def test_cancel_unnamed_single(say, service):
    service.create(None, 10 * M)
    assert (await say("stop the timer")).say == "Timer cancelled."
    assert service.list_active() == []


async def test_cancel_unnamed_ambiguous(say, service):
    service.create("pasta", 10 * M)
    service.create("eggs", 7 * M)
    action = await say("cancel the timer")
    assert action.say == "Which timer? You have eggs and pasta."
    assert len(service.list_active()) == 2


async def test_cancel_all(say, service):
    service.create("pasta", 10 * M)
    service.create("eggs", 7 * M)
    assert (await say("cancel all timers")).say == "All 2 timers cancelled."
    assert service.list_active() == []


async def test_cancel_unknown(say, service):
    service.create("pasta", 10 * M)
    assert (await say("cancel the rice timer")).say == "You don't have a rice timer."
    assert (await say("cancel all timers")).say is not None


async def test_cancel_ringing_timer_stops_alarm(say, service, clock):
    service.create("pasta", M)
    clock.advance(minutes=1)
    service.tick()
    action = await say("cancel the pasta timer", RINGING)
    assert [c.type for c in action.commands] == ["alarm.stop"]


async def test_add_time(say, service, clock):
    pasta = service.create("pasta", 10 * M)
    action = await say("add 5 minutes to the pasta timer")
    assert action.say == "Added 5 minutes. 15 minutes left on the pasta timer."
    assert service.get(pasta.id).duration_s == 15 * M


async def test_add_more_to_single(say, service):
    pasta = service.create("pasta", 10 * M)
    assert (await say("add 2 more minutes")).say == (
        "Added 2 minutes. 12 minutes left on the pasta timer."
    )
    assert service.get(pasta.id).duration_s == 12 * M


async def test_add_time_ambiguous(say, service):
    service.create("pasta", 10 * M)
    service.create("eggs", 7 * M)
    assert (await say("add 2 more minutes")).say == "Which timer? You have eggs and pasta."


async def test_add_time_to_ringing_prefers_ringing_and_stops_alarm(say, service, clock):
    pasta = service.create("pasta", M)
    service.create("eggs", 10 * M)
    clock.advance(minutes=1)
    service.tick()
    action = await say("2 more minutes", RINGING)
    assert action.say == "Added 2 minutes. 2 minutes left on the pasta timer."
    assert [c.type for c in action.commands] == ["alarm.stop"]
    assert service.get(pasta.id).state == "running"


async def test_alarm_stop_dismisses_ringing_only(say, service, clock):
    a = service.create("pasta", M)
    b = service.create("eggs", 10 * M)
    clock.advance(minutes=1)
    service.tick()
    action = await say("thanks coco", RINGING)
    assert action.intent == "alarm.stop"
    assert action.say is None
    assert [c.type for c in action.commands] == ["alarm.stop"]
    assert service.get(a.id).state == "dismissed"
    assert service.get(b.id).state == "running"


async def test_alarm_stop_ignored_when_not_ringing(registry):
    assert registry.match(normalize("stop"), QUIET) is None
