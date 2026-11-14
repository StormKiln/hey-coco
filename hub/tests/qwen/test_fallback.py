import json
from datetime import UTC, datetime

import httpx
import pytest

from coco_hub.intents import builtin
from coco_hub.intents.durations import llm_duration_slot, parse_duration
from coco_hub.intents.engine import (
    UNMATCHED_SAY,
    Intent,
    IntentContext,
    IntentRegistry,
    LlmSlot,
    NoMatch,
    Reply,
    RulesEngine,
)
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import Action, AnswerView, UtteranceContext, UtteranceRequest
from coco_hub.qwen.client import OllamaClient
from coco_hub.qwen.fallback import (
    NOT_SURE_SAY,
    TOO_SLOW_SAY,
    QwenFallback,
    looks_like_question,
    tidy_answer,
)

from fake_ollama import MODEL, FakeOllama, down_transport

pytestmark = pytest.mark.anyio

FIXED = datetime(2026, 11, 14, 20, 5, tzinfo=UTC)


def request(text: str, **context) -> UtteranceRequest:
    ctx = {"view": "idle", "alarm_ringing": False, "media_playing": None} | context
    return UtteranceRequest(text=text, source="voice", context=UtteranceContext(**ctx))


def no_match(text: str) -> NoMatch:
    return NoMatch(text=normalize(text), raw=text)


def timer_handler(match, req, ctx):
    seconds = parse_duration(match.slots["duration"])
    name = match.slots.get("name")
    return Reply(say=f"{name or 'Timer'} set for {seconds} seconds.")


def alarm_handler(match, req, ctx):
    return Reply(say="Alarm off.")


def engine() -> RulesEngine:
    # Only builtins + the test intents below, so these tests don't depend on feature modules.
    registry = IntentRegistry()
    builtin.register(registry)
    registry.register(
        Intent(
            "timer.create",
            patterns=[r"set a timer for (?P<duration>\d+ minutes?)"],
            handler=timer_handler,
            description="start a countdown",
            llm_slots={"name": LlmSlot(), "duration_s": llm_duration_slot()},
        )
    )
    registry.register(
        Intent(
            "alarm.stop",
            patterns=["stop the alarm"],
            handler=alarm_handler,
            applies=lambda c: c.alarm_ringing,
            description="silence a ringing timer alarm",
        )
    )
    return RulesEngine(registry)


@pytest.fixture
def ctx(settings) -> IntentContext:
    return IntentContext(settings=settings, clock=lambda: FIXED)


def fallback(fake: FakeOllama | None = None, *, transport=None, timeout_s=6.0) -> QwenFallback:
    client = OllamaClient(
        "http://ollama.test", MODEL, transport=transport or (fake and fake.transport)
    )
    return QwenFallback(client, engine(), timeout_s=timeout_s)


def reply(intent, slots=None, answer=None):
    return {"intent": intent, "slots": slots or {}, "answer": answer}


async def run(fb: QwenFallback, text: str, ctx, **context) -> Action:
    return await fb(no_match(text), request(text, **context), ctx)


async def test_valid_intent_is_dispatched_to_its_handler(ctx):
    fake = FakeOllama(reply("timer.create", {"name": "eggs", "duration_s": 600}))
    action = await run(fallback(fake), "can you give me ten minutes for the eggs", ctx)
    assert action == Action(
        say="eggs set for 600 seconds.",
        view=None,
        commands=[],
        intent="timer.create",
        matched_by="qwen",
    )
    (chat,) = fake.chats
    assert chat["model"] == MODEL
    assert chat["think"] is False and chat["stream"] is False and chat["keep_alive"] == -1
    assert chat["options"] == {"temperature": 0, "num_ctx": 4096, "num_predict": 200}
    assert chat["format"]["properties"]["intent"]["enum"][-2:] == ["general.answer", "none"]
    assert chat["messages"][0]["role"] == "system"
    assert (
        "- timer.create (name?, duration_s) - start a countdown."
        in (chat["messages"][0]["content"])
    )
    assert chat["messages"][1] == {
        "role": "user",
        "content": "can you give me ten minutes for the eggs",
    }


async def test_builtin_intent_via_qwen(ctx):
    fake = FakeOllama(reply("spell.word", {"word": "Rhythm"}))
    action = await run(fallback(fake), "spelling of rhythm please coco", ctx)
    assert action.say == "Rhythm is spelled R, H, Y, T, H, M."
    assert (action.intent, action.matched_by) == ("spell.word", "qwen")


async def test_general_answer_is_trimmed_to_plain_spoken_text(ctx):
    answer = (
        "**Cats purr** when they're happy 😺. They also purr when stressed.\n"
        "- It may help healing. Scientists still debate it. Some think it's communication."
    )
    fake = FakeOllama(reply("general.answer", {}, answer))
    action = await run(fallback(fake), "why do cats purr", ctx)
    spoken = "Cats purr when they're happy. They also purr when stressed. It may help healing."
    assert action == Action(
        say=spoken,
        view=AnswerView(text=spoken),
        commands=[],
        intent="general.answer",
        matched_by="qwen",
    )


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Canberra.", "Canberra."),
        ("About 28 grams", "About 28 grams."),
        ("It's 176.7 degrees. Roughly 177.", "It's 176.7 degrees. Roughly 177."),
        ("N-E-C-E-S-S-A-R-Y.", "N-E-C-E-S-S-A-R-Y."),
        ("# Steps\n1. Boil.\n2. Peel.\n3. Eat.\n4. Repeat.", "Steps Boil. Peel. Eat."),
        ("- Milk\n- Lemon juice", "Milk Lemon juice."),
        ("Use [milk](https://x.test) and `lemon`!", "Use milk and lemon!"),
        ("🦒🦒", ""),
    ],
)
def test_tidy_answer(raw, spoken):
    assert tidy_answer(raw) == spoken


async def test_empty_answer_after_tidying_says_not_sure(ctx):
    fake = FakeOllama(reply("general.answer", {}, "🤷"))
    action = await run(fallback(fake), "what is the meaning of life", ctx)
    assert action.say == NOT_SURE_SAY
    assert action.intent == "general.answer"


async def test_none_is_a_polite_refusal_and_answer_is_ignored(ctx):
    fake = FakeOllama(reply("none", {}, "I can't make phone calls, but here's a poem..."))
    action = await run(fallback(fake), "call grandma", ctx)
    assert action == Action(
        say=UNMATCHED_SAY, view=None, commands=[], intent="none", matched_by="qwen"
    )


async def test_answer_is_ignored_for_commands(ctx):
    fake = FakeOllama(reply("time.now", {}, "It's probably about noon."))
    action = await run(fallback(fake), "got the time on you", ctx)
    assert action.say == "It's 8:05 PM."
    assert (action.intent, action.matched_by) == ("time.now", "qwen")


async def test_malformed_reply_is_repaired_once(ctx):
    fake = FakeOllama(
        '{"intent": "timer.create", "slots": {"duration_s": ',
        reply("timer.create", {"duration_s": 45}),
    )
    action = await run(fallback(fake), "set one for 45 seconds", ctx)
    assert action.say == "Timer set for 45 seconds."
    assert action.matched_by == "qwen"
    first, repair = fake.chats
    assert repair["messages"][:2] == first["messages"]
    assert repair["messages"][2] == {
        "role": "assistant",
        "content": '{"intent": "timer.create", "slots": {"duration_s": ',
    }
    assert repair["messages"][3]["role"] == "user"
    assert "not valid" in repair["messages"][3]["content"]


async def test_invented_intent_is_invalid_and_repaired(ctx):
    fake = FakeOllama(reply("pizza.order"), reply("none"))
    action = await run(fallback(fake), "get me a pizza", ctx)
    assert (action.say, action.intent, action.matched_by) == (UNMATCHED_SAY, "none", "qwen")
    assert len(fake.chats) == 2


async def test_malformed_twice_not_a_question_says_sorry(ctx):
    fake = FakeOllama("nope", reply("timer.create", {"name": "eggs"}))
    action = await run(fallback(fake), "eggs for a bit", ctx)
    assert action == Action(
        say=UNMATCHED_SAY, view=None, commands=[], intent="none", matched_by="none"
    )
    assert len(fake.chats) == 2


async def test_malformed_twice_question_is_answered_as_general(ctx):
    fake = FakeOllama("garbage", "more garbage", {"answer": "Penguins do have knees."})
    action = await run(fallback(fake), "do penguins have knees", ctx)
    assert action.say == "Penguins do have knees."
    assert (action.intent, action.matched_by) == ("general.answer", "qwen")
    question = fake.chats[2]
    assert question["format"]["required"] == ["answer"]
    assert question["messages"][1]["content"] == "do penguins have knees"


async def test_malformed_twice_question_with_no_answer_says_sorry(ctx):
    fake = FakeOllama("garbage", "more garbage", {"answer": ""})
    action = await run(fallback(fake), "why is the sky blue?", ctx)
    assert (action.say, action.matched_by) == (UNMATCHED_SAY, "none")


async def test_intent_that_does_not_apply_is_refused(ctx):
    fake = FakeOllama(reply("alarm.stop"))
    action = await run(fallback(fake), "make the beeping stop", ctx, alarm_ringing=False)
    assert (action.say, action.intent) == (UNMATCHED_SAY, "none")
    fake = FakeOllama(reply("alarm.stop"))
    action = await run(fallback(fake), "make the beeping stop", ctx, alarm_ringing=True)
    assert (action.say, action.intent, action.matched_by) == ("Alarm off.", "alarm.stop", "qwen")


async def test_timeout_says_it_took_too_long(ctx):
    fake = FakeOllama(("sleep", 5))
    fb = fallback(fake, timeout_s=0.05)
    action = await run(fb, "what does a komodo dragon look like", ctx)
    assert action == Action(
        say=TOO_SLOW_SAY, view=None, commands=[], intent="none", matched_by="none"
    )
    await fb.aclose()  # cancels the background warm-up a timeout schedules


async def test_timeout_covers_the_repair_too(ctx):
    fake = FakeOllama("garbage", ("sleep", 5))
    fb = fallback(fake, timeout_s=0.05)
    action = await run(fb, "eggs for a bit", ctx)
    assert action.say == TOO_SLOW_SAY
    await fb.aclose()


async def test_ollama_down_behaves_like_no_fallback(ctx):
    fb = fallback(transport=down_transport())
    action = await run(fb, "can you give me ten minutes for the eggs", ctx)
    assert action == Action(
        say=UNMATCHED_SAY, view=None, commands=[], intent="none", matched_by="none"
    )


async def test_missing_model_behaves_like_no_fallback(ctx):
    fake = FakeOllama(httpx.Response(404, json={"error": "model 'qwen3:8b' not found"}))
    action = await run(fallback(fake), "why is the sky blue", ctx)
    assert (action.say, action.matched_by) == (UNMATCHED_SAY, "none")


async def test_ollama_server_error_behaves_like_no_fallback(ctx):
    fake = FakeOllama(httpx.Response(500, text="boom"))
    action = await run(fallback(fake), "why is the sky blue", ctx)
    assert (action.say, action.matched_by) == (UNMATCHED_SAY, "none")


async def test_catalog_picks_up_intents_registered_later(ctx):
    fb = fallback(FakeOllama())
    assert "media.pause" not in fb.catalog.intent_names
    fb.engine.registry.register(
        Intent("media.pause", patterns=["pause"], handler=alarm_handler, description="pause")
    )
    assert "media.pause" in fb.catalog.intent_names


async def test_warm_up_primes_the_model_with_the_real_prompt():
    fake = FakeOllama(reply("time.now"), loaded=False)
    fb = fallback(fake)
    await fb.warm_up()
    (chat,) = fake.chats
    assert chat["messages"][0]["content"] == fb.catalog.system_prompt
    assert chat["keep_alive"] == -1


async def test_warm_up_skips_when_model_missing_or_ollama_down(caplog):
    fake = FakeOllama(present=False)
    await fallback(fake).warm_up()
    assert fake.chats == []
    await fallback(transport=down_transport()).warm_up()
    assert "not reachable" in caplog.text


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("why is the sky blue", True),
        ("Hey Coco, how far away is the moon", True),
        ("do penguins have knees", True),
        ("tell me about volcanoes", True),
        ("the moon is far?", True),
        ("call grandma", False),
        ("eggs for a bit", False),
        ("", False),
    ],
)
def test_looks_like_question(text, question):
    assert looks_like_question(text) is question


def test_schema_sent_to_ollama_is_json():
    json.dumps(fallback(FakeOllama()).catalog.schema)
