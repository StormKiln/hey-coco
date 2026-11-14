from datetime import UTC, datetime

import pytest

from coco_hub.intents import builtin
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, AnswerView, UtteranceContext, UtteranceRequest

pytestmark = pytest.mark.anyio


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    builtin.register(r)
    return r


PHRASES = [
    # time.now
    ("what time is it", "time.now", {}),
    ("Hey Coco, what time is it?", "time.now", {}),
    ("What's the time?", "time.now", {}),
    ("whats the time", "time.now", {}),
    ("what time is it now", "time.now", {}),
    ("what time is it right now", "time.now", {}),
    ("can you tell me what time it is", "time.now", {}),
    ("do you know what time it is", "time.now", {}),
    ("tell me the time please", "time.now", {}),
    ("what's the time now", "time.now", {}),
    ("what time", "time.now", {}),
    ("the time", "time.now", {}),
    ("time", "time.now", {}),
    # date.today
    ("what's the date", "date.today", {}),
    ("what is the date today", "date.today", {}),
    ("what's today's date", "date.today", {}),
    ("what day is it", "date.today", {}),
    ("what day is it today", "date.today", {}),
    ("what day is today", "date.today", {}),
    ("what's today", "date.today", {}),
    ("what is today", "date.today", {}),
    ("tell me the date", "date.today", {}),
    ("what date is it", "date.today", {}),
    ("do you know what day it is", "date.today", {}),
    ("today's date", "date.today", {}),
    # not these intents
    ("what time is it in tokyo", None, None),
    ("what time does the store close", None, None),
    ("what day is christmas", None, None),
    ("set a timer for ten minutes", None, None),
    ("what's the weather", None, None),
    ("time for bed", None, None),
]


@pytest.mark.parametrize(("phrase", "intent", "slots"), PHRASES)
def test_phrase_to_intent(registry, phrase, intent, slots):
    match = registry.match(normalize(phrase))
    if intent is None:
        assert match is None
    else:
        assert match is not None
        assert (match.intent, match.slots) == (intent, slots)


def request(text: str) -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        source="voice",
        context=UtteranceContext(view="idle", alarm_ringing=False, media_playing=None),
    )


def context_at(settings, when: datetime, tz: str = "America/Toronto") -> IntentContext:
    return IntentContext(settings=settings.model_copy(update={"timezone": tz}), clock=lambda: when)


@pytest.mark.parametrize(
    ("utc", "tz", "spoken"),
    [
        (datetime(2026, 11, 14, 20, 5, tzinfo=UTC), "America/Toronto", "It's 3:05 PM."),
        (datetime(2026, 11, 14, 5, 0, tzinfo=UTC), "America/Toronto", "It's 12:00 AM."),
        (datetime(2026, 11, 14, 17, 0, tzinfo=UTC), "America/Toronto", "It's 12:00 PM."),
        (datetime(2026, 11, 14, 13, 7, tzinfo=UTC), "UTC", "It's 1:07 PM."),
        (datetime(2026, 11, 14, 9, 59, tzinfo=UTC), "UTC", "It's 9:59 AM."),
        (datetime(2026, 11, 14, 23, 30, tzinfo=UTC), "Europe/London", "It's 11:30 PM."),
    ],
)
async def test_time_now_answer(registry, settings, utc, tz, spoken):
    action = await RulesEngine(registry).handle(
        request("what time is it"), context_at(settings, utc, tz)
    )
    assert action == Action(
        say=spoken,
        view=AnswerView(text=spoken),
        commands=[],
        intent="time.now",
        matched_by="rules",
    )


@pytest.mark.parametrize(
    ("utc", "tz", "spoken"),
    [
        (datetime(2026, 11, 14, 20, 0, tzinfo=UTC), "America/Toronto", "Saturday, November 14th"),
        # 9 PM Toronto on the 14th is already the 15th in UTC.
        (datetime(2026, 11, 15, 2, 0, tzinfo=UTC), "America/Toronto", "Saturday, November 14th"),
        (datetime(2026, 11, 1, 12, 0, tzinfo=UTC), "UTC", "Sunday, November 1st"),
        (datetime(2026, 11, 2, 12, 0, tzinfo=UTC), "UTC", "Monday, November 2nd"),
        (datetime(2026, 11, 3, 12, 0, tzinfo=UTC), "UTC", "Tuesday, November 3rd"),
        (datetime(2026, 11, 4, 12, 0, tzinfo=UTC), "UTC", "Wednesday, November 4th"),
        (datetime(2026, 11, 11, 12, 0, tzinfo=UTC), "UTC", "Wednesday, November 11th"),
        (datetime(2026, 11, 12, 12, 0, tzinfo=UTC), "UTC", "Thursday, November 12th"),
        (datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "UTC", "Friday, November 13th"),
        (datetime(2026, 11, 21, 12, 0, tzinfo=UTC), "UTC", "Saturday, November 21st"),
        (datetime(2026, 11, 22, 12, 0, tzinfo=UTC), "UTC", "Sunday, November 22nd"),
        (datetime(2026, 11, 23, 12, 0, tzinfo=UTC), "UTC", "Monday, November 23rd"),
        (datetime(2026, 12, 31, 12, 0, tzinfo=UTC), "UTC", "Thursday, December 31st"),
    ],
)
async def test_date_today_answer(registry, settings, utc, tz, spoken):
    action = await RulesEngine(registry).handle(
        request("what's the date"), context_at(settings, utc, tz)
    )
    assert action.say == f"It's {spoken}."
    assert action.view == AnswerView(text=f"It's {spoken}.")
    assert (action.intent, action.matched_by) == ("date.today", "rules")
