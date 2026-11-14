import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from coco_hub import db
from coco_hub.intents import media, timers
from coco_hub.intents.engine import IntentContext, IntentRegistry, RulesEngine
from coco_hub.media.policy import AllowAllPolicy, MediaDecision, MediaRequest
from coco_hub.media.youtube import YoutubeService
from coco_hub.protocol import (
    Action,
    MediaCommand,
    UtteranceContext,
    UtteranceRequest,
    VolumeChange,
    VolumeSet,
    YoutubeView,
)

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "youtube"
SEARCH = json.loads((FIXTURES / "search_bluey.json").read_text())
T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)

IDLE = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
YT = UtteranceContext(view="youtube", alarm_ringing=False, media_playing="youtube")
MUSIC = UtteranceContext(view="now_playing", alarm_ringing=False, media_playing="music")
RINGING_YT = UtteranceContext(view="youtube", alarm_ringing=True, media_playing="youtube")


@pytest.fixture
def registry() -> IntentRegistry:
    r = IntentRegistry()
    timers.register(r)
    media.register(r)
    return r


# --- phrase → intent (+ slots) -----------------------------------------------------------

PHRASES: list[tuple[str, UtteranceContext, str | None, dict[str, str]]] = [
    # youtube.play
    ("play Bluey on YouTube", IDLE, "youtube.play", {"query": "bluey"}),
    ("Hey Coco, play Bluey on YouTube please", IDLE, "youtube.play", {"query": "bluey"}),
    ("put on Paw Patrol on YouTube", IDLE, "youtube.play", {"query": "paw patrol"}),
    ("YouTube Cocomelon", IDLE, "youtube.play", {"query": "cocomelon"}),
    ("show me a video of monster trucks", IDLE, "youtube.play", {"query": "monster trucks"}),
    ("show me videos of diggers", IDLE, "youtube.play", {"query": "diggers"}),
    ("find a video about volcanoes", IDLE, "youtube.play", {"query": "volcanoes"}),
    ("watch Peppa Pig on YouTube", IDLE, "youtube.play", {"query": "peppa pig"}),
    ("search YouTube for dinosaur songs", IDLE, "youtube.play", {"query": "dinosaur songs"}),
    ("can you put Bluey on YouTube", IDLE, "youtube.play", {"query": "bluey"}),
    (
        "play the wheels on the bus on youtube",
        IDLE,
        "youtube.play",
        {"query": "the wheels on the bus"},
    ),
    ("play a monster truck video", IDLE, "youtube.play", {"query": "monster truck"}),
    ("play Bluey on YouTube", YT, "youtube.play", {"query": "bluey"}),
    # media.pause
    ("pause", YT, "media.pause", {}),
    ("pause it", YT, "media.pause", {}),
    ("pause the video", YT, "media.pause", {}),
    ("pause the music", MUSIC, "media.pause", {}),
    ("hold on", YT, "media.pause", {}),
    # media.resume
    ("resume", YT, "media.resume", {}),
    ("keep going", YT, "media.resume", {}),
    ("continue", YT, "media.resume", {}),
    ("unpause", YT, "media.resume", {}),
    ("play", YT, "media.resume", {}),
    ("keep playing", MUSIC, "media.resume", {}),
    # media.next
    ("skip", YT, "media.next", {}),
    ("next", YT, "media.next", {}),
    ("next video", YT, "media.next", {}),
    ("skip this one", YT, "media.next", {}),
    ("play the next one", YT, "media.next", {}),
    ("play the next video", YT, "media.next", {}),
    ("next song", MUSIC, "media.next", {}),
    ("play another one", YT, "media.next", {}),
    # media.stop
    ("stop", YT, "media.stop", {}),
    ("stop the video", YT, "media.stop", {}),
    ("stop it", YT, "media.stop", {}),
    ("stop playing", MUSIC, "media.stop", {}),
    ("turn it off", YT, "media.stop", {}),
    ("turn off the video", YT, "media.stop", {}),
    ("cancel the video", YT, "media.stop", {}),
    ("close youtube", YT, "media.stop", {}),
    ("stop", IDLE, "media.stop", {}),
    # volume.set
    ("volume 5", YT, "volume.set", {"level": "5"}),
    ("volume five", YT, "volume.set", {"level": "5"}),
    ("set the volume to 70 percent", YT, "volume.set", {"level": "70", "percent": "percent"}),
    ("set the volume to 7", IDLE, "volume.set", {"level": "7"}),
    ("turn the volume to 3", YT, "volume.set", {"level": "3"}),
    ("volume to 40%", YT, "volume.set", {"level": "40", "percent": "percent"}),
    ("max volume", YT, "volume.set", {"max": "max"}),
    ("mute", YT, "volume.set", {"mute": "mute"}),
    # volume.change
    ("louder", YT, "volume.change", {"up": "louder"}),
    ("turn it up", YT, "volume.change", {"up": "up"}),
    ("turn the volume up", YT, "volume.change", {"up": "up"}),
    ("volume up", YT, "volume.change", {"up": "up"}),
    ("a bit louder", YT, "volume.change", {"up": "louder"}),
    ("I can't hear it", YT, "volume.change", {"up": "cannot hear"}),
    ("quieter", YT, "volume.change", {"down": "quieter"}),
    ("turn it down", YT, "volume.change", {"down": "down"}),
    ("too loud", YT, "volume.change", {"down": "too loud"}),
    ("it's too loud", MUSIC, "volume.change", {"down": "too loud"}),
    ("volume down", IDLE, "volume.change", {"down": "down"}),
    # negatives: the alarm owns "stop" while ringing (media intents step aside)
    ("stop", RINGING_YT, None, {}),
    ("turn it off", RINGING_YT, None, {}),
    ("pause", RINGING_YT, None, {}),
    # negatives: timers keep their phrases
    ("stop the timer", YT, "timer.cancel", {}),
    ("cancel the pasta", YT, "timer.cancel", {"name": "pasta"}),
    # negatives: not media
    ("play", IDLE, "media.resume", {}),
    ("play Bluey", IDLE, None, {}),
    ("show me a picture of a cat", IDLE, None, {}),
    ("what is the weather", IDLE, None, {}),
    ("youtube", IDLE, None, {}),
    ("next timer", YT, None, {}),
    ("stop the alarm", YT, None, {}),
]


@pytest.mark.parametrize(("text", "context", "intent", "slots"), PHRASES)
def test_phrases(registry, text, context, intent, slots):
    from coco_hub.intents.normalize import normalize

    match = registry.match(normalize(text), context)
    if intent is None:
        assert match is None or not match.intent.startswith(("media.", "youtube.", "volume.")), (
            match
        )
        return
    assert match is not None, text
    assert match.intent == intent
    for key, value in slots.items():
        assert match.slots.get(key) == value, (text, match.slots)


def test_phrase_table_is_big_enough():
    assert len(PHRASES) >= 40


def test_media_intents_have_descriptions(registry):
    for intent in registry:
        if intent.name.startswith(("media.", "youtube.", "volume.")):
            assert len(intent.description) > 20, intent.name


# --- handlers ----------------------------------------------------------------------------


class SpyPolicy:
    def __init__(self, decide=None) -> None:
        self.requests: list[MediaRequest] = []
        self.decide = decide or (lambda r: MediaDecision.allow())

    def check(self, request: MediaRequest) -> MediaDecision:
        self.requests.append(request)
        return self.decide(request)


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=SEARCH)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def youtube(conn, recorder) -> YoutubeService:
    return YoutubeService(
        conn,
        api_key="test-key",
        region="CA",
        daily_budget=9000,
        clock=lambda: T0,
        transport=httpx.MockTransport(recorder),
    )


@pytest.fixture
def policy() -> SpyPolicy:
    return SpyPolicy()


@pytest.fixture
def run(registry, settings, youtube, policy):
    engine = RulesEngine(registry)
    ctx = IntentContext(
        settings=settings,
        clock=lambda: T0,
        services={"youtube": youtube, "media_policy": policy},
    )

    async def _run(text: str, context: UtteranceContext = IDLE) -> Action:
        result = await engine.handle(
            UtteranceRequest(text=text, source="voice", context=context), ctx
        )
        assert isinstance(result, Action), result
        return result

    return _run


async def test_play_shows_top_result(run, recorder):
    action = await run("play Bluey on YouTube")
    assert action.intent == "youtube.play"
    assert action.view == YoutubeView(
        video_id="a1B2c3D4e5F",
        title="Bluey Full Episodes | Season 3 | Rug Island, Dad Baby & more | Bluey",
    )
    assert action.say == "Playing Bluey Full Episodes."
    assert recorder.requests[0].url.params["q"] == "bluey"


async def test_play_goes_through_the_policy(run, policy):
    await run("play Bluey on YouTube")
    assert policy.requests == [
        MediaRequest(kind="youtube", query="bluey"),
        MediaRequest(
            kind="youtube",
            query="bluey",
            item_id="a1B2c3D4e5F",
            title="Bluey Full Episodes | Season 3 | Rug Island, Dad Baby & more | Bluey",
            channel="Bluey - Official Channel",
        ),
    ]


async def test_deny_policy_blocks_with_its_reason(run, policy, recorder):
    policy.decide = lambda r: MediaDecision.deny("Let's ask a grown-up first.")
    action = await run("play Bluey on YouTube")
    assert action.say == "Let's ask a grown-up first."
    assert action.view is None and action.commands == []
    # A query-level deny happens before any search, so no quota is spent.
    assert recorder.requests == []


async def test_policy_can_deny_one_video_and_skip_to_the_next(run, policy):
    policy.decide = lambda r: (
        MediaDecision.deny("Not that one.") if r.item_id == "a1B2c3D4e5F" else MediaDecision.allow()
    )
    action = await run("play Bluey on YouTube")
    assert action.view == YoutubeView(video_id="Zz9Yy8Xx7Ww", title="Bluey's Big Play")


async def test_every_video_denied_speaks_the_first_reason(run, policy):
    policy.decide = lambda r: (
        MediaDecision.allow() if r.item_id is None else MediaDecision.deny("Not today.")
    )
    action = await run("play Bluey on YouTube")
    assert action.say == "Not today."
    assert action.view is None


async def test_deny_without_reason_has_a_default(run, policy):
    policy.decide = lambda r: MediaDecision(allowed=False)
    action = await run("play Bluey on YouTube")
    assert action.say == "Sorry, I can't play that."


async def test_next_plays_the_next_result_through_the_policy(run, policy):
    await run("play Bluey on YouTube")
    policy.requests.clear()
    action = await run("next video", YT)
    assert action.intent == "media.next"
    assert action.view == YoutubeView(video_id="Zz9Yy8Xx7Ww", title="Bluey's Big Play")
    assert action.say == "Playing Bluey's Big Play."
    assert [r.item_id for r in policy.requests] == ["Zz9Yy8Xx7Ww"]
    action = await run("skip", YT)
    assert action.view == YoutubeView(
        video_id="Kk1Ll2Mm3Nn", title='"Keepy Uppy" - Bluey Song Sing-Along'
    )
    action = await run("skip", YT)
    assert action.view is None
    assert action.say == "That's all the videos I found."


async def test_next_skips_denied_results(run, policy):
    await run("play Bluey on YouTube")
    policy.decide = lambda r: (
        MediaDecision.deny("No.") if r.item_id == "Zz9Yy8Xx7Ww" else MediaDecision.allow()
    )
    action = await run("next", YT)
    assert action.view is not None and action.view.video_id == "Kk1Ll2Mm3Nn"


async def test_next_without_earlier_search(run):
    action = await run("next", YT)
    assert action.view is None
    assert action.say == "That's all the videos I found."


async def test_next_for_music_is_a_command(run, policy):
    action = await run("next song", MUSIC)
    assert action.commands == [MediaCommand(type="media.next", target="music")]
    assert action.say is None
    assert policy.requests == []


@pytest.mark.parametrize(
    ("text", "command"),
    [("pause", "media.pause"), ("keep going", "media.resume"), ("stop the video", "media.stop")],
)
async def test_controls_target_what_is_playing(run, text, command):
    action = await run(text, YT)
    assert action.commands == [MediaCommand(type=command, target="youtube")]
    assert action.say is None
    action = await run(text, MUSIC)
    assert action.commands == [MediaCommand(type=command, target="music")]


@pytest.mark.parametrize("text", ["pause", "resume", "skip", "stop"])
async def test_controls_with_nothing_playing(run, text):
    action = await run(text, IDLE)
    assert action.say == "Nothing's playing."
    assert action.commands == []


async def test_missing_key_is_spoken(registry, settings, conn, policy):
    engine = RulesEngine(registry)
    youtube = YoutubeService(conn, api_key=None, region="CA", daily_budget=9000)
    ctx = IntentContext(settings=settings, services={"youtube": youtube, "media_policy": policy})
    action = await engine.handle(
        UtteranceRequest(text="play bluey on youtube", source="voice", context=IDLE), ctx
    )
    assert action.say == "YouTube isn't set up yet."


async def test_no_results(registry, settings, conn, policy):
    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"kind": "youtube#searchListResponse", "items": []})

    youtube = YoutubeService(
        conn, api_key="k", region="CA", daily_budget=9000, transport=httpx.MockTransport(empty)
    )
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, services={"youtube": youtube, "media_policy": policy})
    action = await engine.handle(
        UtteranceRequest(text="play zzqqxx on youtube", source="voice", context=IDLE), ctx
    )
    assert action.say == "I couldn't find that on YouTube."
    assert action.view is None


async def test_missing_policy_fails_closed(registry, settings, youtube):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, services={"youtube": youtube})
    with pytest.raises(KeyError):
        await engine.handle(
            UtteranceRequest(text="play bluey on youtube", source="voice", context=IDLE), ctx
        )


def test_allow_all_policy_allows():
    decision = AllowAllPolicy().check(MediaRequest(kind="youtube", query="anything"))
    assert decision.allowed and decision.reason is None


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("volume 5", 50),
        ("volume 10", 100),
        ("volume 0", 0),
        ("set the volume to 70 percent", 70),
        ("volume 70", 70),
        ("volume to 40%", 40),
        ("volume 2.5", 25),
        ("max volume", 100),
        ("mute", 0),
    ],
)
async def test_volume_set(run, text, level):
    action = await run(text, YT)
    assert action.commands == [VolumeSet(level=level)]


async def test_volume_out_of_range(run):
    action = await run("volume 150", YT)
    assert action.commands == []
    assert action.say == "Volume goes from 0 to 10."


@pytest.mark.parametrize(
    ("text", "delta"),
    [("louder", 15), ("I can't hear it", 15), ("turn it down", -15), ("too loud", -15)],
)
async def test_volume_change(run, text, delta):
    action = await run(text, IDLE)
    assert action.commands == [VolumeChange(delta=delta)]
    assert action.say is None
