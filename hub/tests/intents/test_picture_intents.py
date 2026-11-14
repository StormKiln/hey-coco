import json
from pathlib import Path

import httpx
import pytest

from coco_hub.intents import builtin, pictures, timers
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.pictures.commons import CommonsClient, PictureService
from coco_hub.protocol import Action, PictureView, UtteranceContext, UtteranceRequest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "commons"
IDLE = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
PICTURE = UtteranceContext(view="picture", alarm_ringing=False, media_playing=None)


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    builtin.register(r)
    timers.register(r)
    pictures.register(r)
    return r


PHRASES: list[tuple[str, UtteranceContext, str | None, str | None]] = [
    # picture.show
    ("show me a fire truck", IDLE, "picture.show", "fire truck"),
    ("Show me a picture of a fire truck", IDLE, "picture.show", "fire truck"),
    ("show me pictures of penguins", IDLE, "picture.show", "penguins"),
    ("show me some penguins", IDLE, "picture.show", "penguins"),
    ("what does an axolotl look like", IDLE, "picture.show", "axolotl"),
    ("What do axolotls look like?", IDLE, "picture.show", "axolotls"),
    ("what does the eiffel tower look like", IDLE, "picture.show", "eiffel tower"),
    ("can I see a picture of a red panda", IDLE, "picture.show", "red panda"),
    ("can we see a photo of a giraffe", IDLE, "picture.show", "giraffe"),
    ("could I see some pictures of volcanoes", IDLE, "picture.show", "volcanoes"),
    ("Hey Coco, show me a tiger", IDLE, "picture.show", "tiger"),
    ("show us an octopus", IDLE, "picture.show", "octopus"),
    ("show me an image of a blue whale please", IDLE, "picture.show", "blue whale"),
    ("show me a photo of the moon", IDLE, "picture.show", "moon"),
    ("picture of a koala", IDLE, "picture.show", "koala"),
    ("pictures of dinosaurs", IDLE, "picture.show", "dinosaurs"),
    ("find me a picture of a hedgehog", IDLE, "picture.show", "hedgehog"),
    ("search for pictures of rainbows", IDLE, "picture.show", "rainbows"),
    ("I want to see a sloth", IDLE, "picture.show", "sloth"),
    ("let's see a monster truck", IDLE, "picture.show", "monster truck"),
    ("can I see a cheetah", IDLE, "picture.show", "cheetah"),
    ("show me what a narwhal looks like", IDLE, "picture.show", "narwhal"),
    ("show me a fire truck", PICTURE, "picture.show", "fire truck"),
    # picture.next — only while pictures are on screen
    ("next", PICTURE, "picture.next", None),
    ("next one", PICTURE, "picture.next", None),
    ("next picture", PICTURE, "picture.next", None),
    ("show me another", PICTURE, "picture.next", None),
    ("show me another one", PICTURE, "picture.next", None),
    ("another one", PICTURE, "picture.next", None),
    ("a different one", PICTURE, "picture.next", None),
    ("show me the next one please", PICTURE, "picture.next", None),
    ("more", PICTURE, "picture.next", None),
    ("next one", IDLE, None, None),
    ("show me another", IDLE, None, None),
    ("next", IDLE, None, None),
    # not pictures
    ("show me the timers", IDLE, "timer.list", None),
    ("show me how to make pancakes", IDLE, None, None),
    ("show me a video of a cat", IDLE, None, None),
    ("show me videos of trains", IDLE, None, None),
    ("show me the weather", IDLE, None, None),
    ("show me my timers", IDLE, "timer.list", None),
    ("what time is it", IDLE, "time.now", None),
    ("what does a cow say", IDLE, None, None),
]


@pytest.mark.parametrize(("phrase", "context", "intent", "query"), PHRASES)
def test_phrase_to_intent(registry, phrase, context, intent, query):
    match = registry.match(normalize(phrase), context)
    if intent is None:
        assert match is None or not match.intent.startswith("picture."), match
        return
    assert match is not None, f"{phrase!r} matched nothing"
    assert match.intent == intent
    if query is not None:
        assert match.slots.get("query") == query


def test_enough_phrases():
    assert sum(1 for p in PHRASES if p[2] == "picture.show") >= 20
    assert len(PHRASES) >= 25


def test_descriptions_and_slots_for_the_fallback(registry):
    show, nxt = registry.get("picture.show"), registry.get("picture.next")
    assert show is not None and nxt is not None
    assert show.slot_names == ("query",)
    assert "picture" in show.description.lower() and "query" in show.description
    assert nxt.description


@pytest.mark.parametrize(
    ("query", "say"),
    [
        ("fire truck", "Here's a fire truck."),
        ("axolotl", "Here's an axolotl."),
        ("penguins", "Here are some penguins."),
        ("octopus", "Here's an octopus."),
        ("bus", "Here's a bus."),
        ("moon", "Here's a moon."),
    ],
)
def test_spoken_reply(query, say):
    assert pictures.here_is(query) == say


# --- handlers ----------------------------------------------------------------------------


class FakeCommons:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        terms = request.url.params["gsrsearch"]
        self.calls.append(terms)
        if self.fail:
            return httpx.Response(503)
        if terms == '"fire truck" filetype:bitmap':
            return httpx.Response(200, json=json.loads((FIXTURES / "fire_truck.json").read_text()))
        return httpx.Response(200, json={"batchcomplete": True})


@pytest.fixture
def commons() -> FakeCommons:
    return FakeCommons()


@pytest.fixture
def service(commons) -> PictureService:
    return PictureService(CommonsClient(transport=httpx.MockTransport(commons)))


@pytest.fixture
def say(registry, service, settings):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, services={"pictures": service})

    async def run(text: str, context: UtteranceContext = IDLE) -> Action:
        result = await engine.handle(
            UtteranceRequest(text=text, source="voice", context=context), ctx
        )
        assert isinstance(result, Action), f"{text!r} did not match"
        return result

    return run


@pytest.mark.anyio
async def test_show_picture(say, service):
    action = await say("show me a fire truck")
    assert action.say == "Here's a fire truck."
    assert isinstance(action.view, PictureView)
    assert action.view.query == "fire truck"
    assert len(action.view.images) == 8
    assert action.intent == "picture.show" and action.matched_by == "rules"
    assert service.last is not None and service.last.query == "fire truck"


@pytest.mark.anyio
async def test_nothing_found(say, service, commons):
    action = await say("show me a zzyzx quux")
    assert action.say == "I couldn't find a picture of that."
    assert action.view is None
    assert service.last is None


@pytest.mark.anyio
async def test_fetch_error(say, commons):
    commons.fail = True
    action = await say("show me a fire truck")
    assert action.say == "Sorry, I can't get pictures right now."
    assert action.view is None


@pytest.mark.anyio
async def test_unsuitable_query_is_not_searched(say, commons):
    action = await say("show me naked people")
    assert action.say == "I couldn't find a picture of that."
    assert commons.calls == []


@pytest.mark.anyio
async def test_next_rotates_through_results(say):
    first = await say("show me a fire truck")
    assert isinstance(first.view, PictureView)
    images = first.view.images
    nxt = await say("next one", PICTURE)
    assert nxt.intent == "picture.next"
    assert isinstance(nxt.view, PictureView)
    assert nxt.view.query == "fire truck"
    assert nxt.view.images == images[1:] + images[:1]
    assert nxt.say is None
    again = await say("show me another", PICTURE)
    assert isinstance(again.view, PictureView)
    assert again.view.images[0] == images[2]


@pytest.mark.anyio
async def test_next_with_nothing_remembered(say):
    action = await say("next", PICTURE)
    assert action.intent == "picture.next"
    assert action.view is None
    assert action.say == "Ask me to show you something first."


@pytest.mark.anyio
async def test_next_with_only_one_picture(say, service):
    first = await say("show me a fire truck")
    assert isinstance(first.view, PictureView)
    service.remember("fire truck", first.view.images[:1])
    action = await say("next", PICTURE)
    assert action.say == "That's the only picture I found."
    assert action.view is None


@pytest.mark.anyio
async def test_qwen_style_dispatch_with_slot(registry, service, settings):
    from coco_hub.intents.engine import Match

    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, services={"pictures": service})
    request = UtteranceRequest(text="i wanna see a fire engine thing", source="voice", context=IDLE)
    action = await engine.dispatch(
        Match("picture.show", {"query": "fire truck"}, request.text), request, ctx, "qwen"
    )
    assert action.say == "Here's a fire truck." and action.matched_by == "qwen"
