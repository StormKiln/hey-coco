import pytest

from coco_hub.intents import builtin
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import AnswerView, UtteranceContext, UtteranceRequest

pytestmark = pytest.mark.anyio


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    builtin.register(r)
    return r


def request(text: str) -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        source="voice",
        context=UtteranceContext(view="idle", alarm_ringing=False, media_playing=None),
    )


SPELLING = [
    ("how do you spell necessary", "Necessary is spelled N, E, C, E, S, S, A, R, Y."),
    ("Hey Coco, how do you spell rhythm?", "Rhythm is spelled R, H, Y, T, H, M."),
    ("can you spell beautiful for me", "Beautiful is spelled B, E, A, U, T, I, F, U, L."),
    ("spell cat", "Cat is spelled C, A, T."),
    ("how is restaurant spelled", "Restaurant is spelled R, E, S, T, A, U, R, A, N, T."),
    ("how is colour spelt", "Colour is spelled C, O, L, O, U, R."),
    ("what's the spelling of the word friend", "Friend is spelled F, R, I, E, N, D."),
    ("how do I spell Wednesday", "Wednesday is spelled W, E, D, N, E, S, D, A, Y."),
    ("how to spell because", "Because is spelled B, E, C, A, U, S, E."),
    # number words are turned into digits by normalisation; spell what was said
    ("how do you spell one", "One is spelled O, N, E."),
    ("how do you spell twenty two", "Twenty two is spelled T, W, E, N, T, Y, space, T, W, O."),
    ("how do you spell ice cream", "Ice cream is spelled I, C, E, space, C, R, E, A, M."),
]


@pytest.mark.parametrize(("phrase", "spoken"), SPELLING)
async def test_spelling(registry, settings, phrase, spoken):
    action = await RulesEngine(registry).handle(request(phrase), IntentContext(settings=settings))
    assert (action.intent, action.matched_by) == ("spell.word", "rules")
    assert action.say == spoken
    assert action.view == AnswerView(text=spoken)


@pytest.mark.parametrize(
    "phrase",
    ["spell", "how do you spell", "what time is it", "spell the whole alphabet backwards please"],
)
def test_not_spelling(registry, phrase):
    match = registry.match(normalize(phrase))
    assert match is None or match.intent != "spell.word"


def test_spelled_handles_junk():
    assert builtin.spelled("  ") == "Sorry, I didn't catch the word."
    assert builtin.spelled("Don't") == "Dont is spelled D, O, N, T."
