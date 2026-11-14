import re
from datetime import UTC, datetime

import pytest

from coco_hub.intents.engine import (
    Intent,
    IntentContext,
    IntentRegistry,
    Match,
    NoMatch,
    Reply,
    RulesEngine,
)
from coco_hub.protocol import Action, AnswerView, UtteranceContext, UtteranceRequest

pytestmark = pytest.mark.anyio

FIXED = datetime(2026, 11, 14, 20, 5, tzinfo=UTC)


def request(text: str, **context) -> UtteranceRequest:
    ctx = {"view": "idle", "alarm_ringing": False, "media_playing": None} | context
    return UtteranceRequest(text=text, source="voice", context=UtteranceContext(**ctx))


@pytest.fixture
def ctx(settings) -> IntentContext:
    return IntentContext(settings=settings, clock=lambda: FIXED, services={"answer": 42})


def say(text: str):
    def handler(match, req, ctx):
        return Reply(say=text)

    return handler


def test_register_and_match_string_pattern():
    registry = IntentRegistry()
    registry.register(Intent("demo.hello", patterns=["hello( there)?"], handler=say("hi")))
    assert registry.match("hello there") == Match(intent="demo.hello", slots={}, text="hello there")


def test_patterns_must_match_the_whole_utterance():
    registry = IntentRegistry()
    registry.register(Intent("time.now", patterns=["what time is it"], handler=say("x")))
    assert registry.match("what time is it in tokyo") is None
    assert registry.match("so what time is it") is None


def test_named_groups_become_slots():
    registry = IntentRegistry()
    registry.register(
        Intent("timer.create", patterns=[r"set an? (?P<name>\w+) timer"], handler=say("x"))
    )
    assert registry.match("set a pasta timer").slots == {"name": "pasta"}


def test_unmatched_optional_groups_are_dropped_from_slots():
    registry = IntentRegistry()
    registry.register(Intent("x", patterns=[r"go(?: to (?P<where>\w+))?"], handler=say("x")))
    assert registry.match("go").slots == {}
    assert registry.match("go to bed").slots == {"where": "bed"}


def test_callable_matcher():
    def matcher(text: str) -> dict[str, str] | None:
        return {"animal": text.removesuffix(" noise")} if text.endswith(" noise") else None

    registry = IntentRegistry()
    registry.register(Intent("sound.play", patterns=[matcher], handler=say("moo")))
    assert registry.match("cow noise") == Match("sound.play", {"animal": "cow"}, "cow noise")
    assert registry.match("cow") is None


def test_precompiled_patterns_are_accepted():
    registry = IntentRegistry()
    registry.register(Intent("x", patterns=[re.compile("stop")], handler=say("x")))
    assert registry.match("stop").intent == "x"


def test_duplicate_intent_name_rejected():
    registry = IntentRegistry()
    registry.register(Intent("x", patterns=["a"], handler=say("x")))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Intent("x", patterns=["b"], handler=say("x")))


def test_intent_requires_patterns():
    with pytest.raises(ValueError, match="at least one pattern"):
        Intent("x", patterns=[], handler=say("x"))


def test_bad_pattern_type_rejected():
    with pytest.raises(TypeError):
        Intent("x", patterns=[42], handler=say("x"))  # type: ignore[list-item]


def test_higher_priority_wins_regardless_of_registration_order():
    registry = IntentRegistry()
    registry.register(Intent("media.stop", patterns=["stop"], handler=say("x")))
    registry.register(Intent("alarm.stop", patterns=["stop"], handler=say("x"), priority=10))
    assert registry.match("stop").intent == "alarm.stop"


def test_more_specific_wins_at_equal_priority():
    registry = IntentRegistry()
    registry.register(Intent("youtube.play", patterns=[r"play (?P<query>.+)"], handler=say("x")))
    registry.register(Intent("music.resume", patterns=["play music"], handler=say("x")))
    assert registry.match("play music").intent == "music.resume"
    assert registry.match("play baby shark").intent == "youtube.play"


def test_priority_beats_specificity():
    registry = IntentRegistry()
    registry.register(Intent("literal", patterns=["play music"], handler=say("x")))
    registry.register(Intent("greedy", patterns=[r"play (?P<q>.+)"], handler=say("x"), priority=1))
    assert registry.match("play music").intent == "greedy"


def test_full_tie_goes_to_first_registered():
    registry = IntentRegistry()
    registry.register(Intent("first", patterns=["stop"], handler=say("x")))
    registry.register(Intent("second", patterns=["stop"], handler=say("x")))
    assert registry.match("stop").intent == "first"


def test_best_pattern_within_an_intent_is_used():
    registry = IntentRegistry()
    registry.register(
        Intent("x", patterns=[r"(?P<what>.+) please", r"(?P<what>stop) please"], handler=say("x"))
    )
    other = Intent("y", patterns=[r"(?P<a>stop) (?P<b>please)"], handler=say("y"))
    registry.register(other)
    # x's best pattern leaves more literal text ("please") than y's, so x is more specific.
    assert registry.match("stop please").intent == "x"


def test_guard_limits_intent_to_context():
    registry = IntentRegistry()
    registry.register(
        Intent(
            "alarm.stop",
            patterns=["stop"],
            handler=say("x"),
            priority=10,
            applies=lambda c: c.alarm_ringing,
        )
    )
    registry.register(Intent("media.stop", patterns=["stop"], handler=say("x")))
    ringing = UtteranceContext(view="timers", alarm_ringing=True, media_playing=None)
    quiet = UtteranceContext(view="idle", alarm_ringing=False, media_playing="music")
    assert registry.match("stop", ringing).intent == "alarm.stop"
    assert registry.match("stop", quiet).intent == "media.stop"
    # Without a context, guarded intents are skipped.
    assert registry.match("stop").intent == "media.stop"


def test_registry_lists_intents_and_slot_names():
    registry = IntentRegistry()
    registry.register(
        Intent(
            "timer.create",
            patterns=[r"set an? (?P<name>\w+) timer", r"timer for (?P<duration>.+)"],
            handler=say("x"),
            description="Start a timer",
        )
    )
    (intent,) = registry.intents
    assert registry.get("timer.create") is intent
    assert registry.get("nope") is None
    assert intent.slot_names == ("name", "duration")
    assert intent.description == "Start a timer"
    assert "timer.create" in registry
    assert len(registry) == 1


def test_context_now_is_in_hub_timezone(settings):
    toronto = settings.model_copy(update={"timezone": "America/Toronto"})
    ctx = IntentContext(settings=toronto, clock=lambda: FIXED)
    assert ctx.now().isoformat() == "2026-11-14T15:05:00-05:00"
    assert ctx.services == {}


async def test_engine_returns_action_stamped_with_intent_and_provenance(ctx):
    registry = IntentRegistry()
    registry.register(
        Intent(
            "demo.hi",
            patterns=["hi"],
            handler=lambda m, r, c: Reply(say="Hello.", view=AnswerView(text="Hello.")),
        )
    )
    result = await RulesEngine(registry).handle(request("Hey Coco, hi!"), ctx)
    assert result == Action(
        say="Hello.",
        view=AnswerView(text="Hello."),
        commands=[],
        intent="demo.hi",
        matched_by="rules",
    )


async def test_engine_returns_no_match_with_normalized_text(ctx):
    result = await RulesEngine(IntentRegistry()).handle(request("Hey Coco, sing me a song!"), ctx)
    assert result == NoMatch(text="sing me a song", raw="Hey Coco, sing me a song!")


async def test_handler_sees_slots_request_context_and_services(ctx):
    seen = {}

    def handler(match, req, c):
        seen.update(slots=match.slots, media=req.context.media_playing, svc=c.services["answer"])
        return Reply(say=None)

    registry = IntentRegistry()
    registry.register(Intent("media.next", patterns=[r"next (?P<what>\w+)"], handler=handler))
    await RulesEngine(registry).handle(request("next song", media_playing="music"), ctx)
    assert seen == {"slots": {"what": "song"}, "media": "music", "svc": 42}


async def test_async_handlers_are_awaited(ctx):
    async def handler(match, req, c):
        return Reply(say="async")

    registry = IntentRegistry()
    registry.register(Intent("x", patterns=["go"], handler=handler))
    result = await RulesEngine(registry).handle(request("go"), ctx)
    assert result.say == "async"


async def test_dispatch_lets_fallbacks_reuse_handlers(ctx):
    registry = IntentRegistry()
    registry.register(
        Intent("timer.create", patterns=[r"timer (?P<n>\d+)"], handler=say("Timer set."))
    )
    engine = RulesEngine(registry)
    match = Match(intent="timer.create", slots={"n": "5"}, text="start me a 5 timer")
    action = await engine.dispatch(match, request("start me a 5 timer"), ctx, matched_by="qwen")
    assert (action.intent, action.matched_by, action.say) == ("timer.create", "qwen", "Timer set.")


async def test_dispatch_unknown_intent_raises(ctx):
    engine = RulesEngine(IntentRegistry())
    with pytest.raises(KeyError):
        await engine.dispatch(Match("nope", {}, "x"), request("x"), ctx)
