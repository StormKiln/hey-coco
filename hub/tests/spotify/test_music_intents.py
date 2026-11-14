from datetime import UTC, datetime

import pytest

from coco_hub import db
from coco_hub.intents import build_registry
from coco_hub.intents.engine import IntentContext, RulesEngine
from coco_hub.intents.music import parse_music_request, spoken_name
from coco_hub.intents.normalize import normalize
from coco_hub.media.policy import MediaDecision, MediaRequest
from coco_hub.media.spotify.service import SpotifyService
from coco_hub.media.youtube import YoutubeService
from coco_hub.protocol import (
    Action,
    MediaCommand,
    NowPlayingView,
    UtteranceContext,
    UtteranceRequest,
    VolumeChange,
    VolumeSet,
)

from fake_spotify import IPAD_ID, FakeSpotify, json_response, log_in, spotify_settings

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
IDLE = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
YT = UtteranceContext(view="youtube", alarm_ringing=False, media_playing="youtube")
MUSIC = UtteranceContext(view="now_playing", alarm_ringing=False, media_playing="music")
SOUND = UtteranceContext(view="sound", alarm_ringing=False, media_playing="sound")


@pytest.fixture(scope="module")
def registry():
    return build_registry()


# --- phrase → intent ---------------------------------------------------------------------

PHRASES: list[tuple[str, UtteranceContext, str | None, dict[str, str]]] = [
    # music.play
    ("play Raffi", IDLE, "music.play", {"query": "raffi"}),
    ("Hey Coco, play Raffi please", IDLE, "music.play", {"query": "raffi"}),
    ("play some music", IDLE, "music.play", {"query": "some music"}),
    ("play music", IDLE, "music.play", {"query": "music"}),
    ("play the Frozen soundtrack", IDLE, "music.play", {"query": "the frozen soundtrack"}),
    ("put on my Discover Weekly", IDLE, "music.play", {"query": "my discover weekly"}),
    ("play Taylor Swift on Spotify", IDLE, "music.play", {"query": "taylor swift"}),
    ("play songs by the Wiggles", IDLE, "music.play", {"query": "songs by the wiggles"}),
    ("play Baby Shark", IDLE, "music.play", {"query": "baby shark"}),
    ("play the Baby Shark song", IDLE, "music.play", {"query": "the baby shark song"}),
    ("put on some Raffi", IDLE, "music.play", {"query": "some raffi"}),
    ("I want to hear Let It Go", IDLE, "music.play", {"query": "let it go"}),
    ("listen to the Moana album", IDLE, "music.play", {"query": "the moana album"}),
    ("play my dinner playlist", IDLE, "music.play", {"query": "my dinner playlist"}),
    ("play Raffi on Spotify", YT, "music.play", {"query": "raffi"}),
    ("play Raffi", MUSIC, "music.play", {"query": "raffi"}),
    ("can you play the Beatles", IDLE, "music.play", {"query": "the beatles"}),
    ("play some Christmas music", IDLE, "music.play", {"query": "some christmas music"}),
    ("spotify play Raffi", IDLE, "music.play", {"query": "raffi"}),
    # music.now_playing
    ("what's playing", MUSIC, "music.now_playing", {}),
    ("what song is this", MUSIC, "music.now_playing", {}),
    ("what is this song", IDLE, "music.now_playing", {}),
    ("who sings this", MUSIC, "music.now_playing", {}),
    ("what song is playing", MUSIC, "music.now_playing", {}),
    ("who is this", MUSIC, "music.now_playing", {}),
    ("what's this song called", MUSIC, "music.now_playing", {}),
    ("what is the name of this song", MUSIC, "music.now_playing", {}),
    # negatives: YouTube keeps "on youtube" / videos
    ("play bluey on youtube", IDLE, "youtube.play", {"query": "bluey"}),
    ("play Baby Shark on YouTube", IDLE, "youtube.play", {"query": "baby shark"}),
    ("play a monster truck video", IDLE, "youtube.play", {}),
    ("show me a video of trains", IDLE, "youtube.play", {}),
    # negatives: transport keeps its phrases
    ("play it again", MUSIC, "media.resume", {}),
    ("play", MUSIC, "media.resume", {}),
    ("play the music", MUSIC, "media.resume", {}),
    ("play the next song", MUSIC, "media.next", {}),
    ("play another one", MUSIC, "media.next", {}),
    ("play something else", MUSIC, "media.next", {}),
    ("pause the music", MUSIC, "media.pause", {}),
    ("next song", MUSIC, "media.next", {}),
    ("turn it up", MUSIC, "volume.change", {}),
    # negatives: sounds, pictures, games and timers are not music
    ("play a cow sound", IDLE, None, {}),
    ("play the sound of a fire truck", IDLE, None, {}),
    ("play the cow noise", IDLE, None, {}),
    ("play a dog sound", SOUND, None, {}),
    ("show me a picture of a cat", IDLE, None, {}),
    ("play a game", IDLE, None, {}),
    ("play the next episode", YT, "media.next", {}),
    ("set a timer for 5 minutes", IDLE, "timer.create", {}),
    ("what is the weather", IDLE, "weather.current", {}),
    ("stop", MUSIC, "media.stop", {}),
]


@pytest.mark.parametrize(("text", "context", "intent", "slots"), PHRASES)
def test_phrases(registry, text, context, intent, slots):
    match = registry.match(normalize(text), context)
    if intent is None:
        assert match is None or not match.intent.startswith("music."), match
        return
    assert match is not None, text
    assert match.intent == intent, (text, match)
    for key, value in slots.items():
        assert match.slots.get(key) == value, (text, match.slots)


def test_phrase_table_is_big_enough():
    assert len(PHRASES) >= 40


@pytest.mark.parametrize(
    ("query", "kind", "search"),
    [
        ("raffi", "any", "raffi"),
        ("some raffi", "any", "raffi"),
        ("some music", "any", ""),
        ("music", "any", ""),
        ("something", "any", ""),
        ("some songs", "any", ""),
        ("my music", "any", ""),
        ("the frozen soundtrack", "album", "frozen soundtrack"),
        ("the moana album", "album", "moana"),
        ("the album 1989", "album", "1989"),
        ("my discover weekly", "playlist", "discover weekly"),
        ("my dinner playlist", "playlist", "dinner"),
        ("the playlist road trip", "playlist", "road trip"),
        ("songs by the wiggles", "artist", "the wiggles"),
        ("music by raffi", "artist", "raffi"),
        ("some music by raffi", "artist", "raffi"),
        ("the baby shark song", "song", "baby shark"),
        ("the song let it go", "song", "let it go"),
        ("baby shark", "any", "baby shark"),
        ("the beatles", "any", "the beatles"),
        ("some christmas music", "any", "christmas"),
        ("wiggles songs", "any", "wiggles"),
    ],
)
def test_parse_music_request(query, kind, search):
    assert parse_music_request(query) == (kind, search)


@pytest.mark.parametrize(
    ("name", "spoken"),
    [
        ("Frozen (Original Motion Picture Soundtrack)", "Frozen"),
        ("Here Comes the Sun - Remastered 2009", "Here Comes the Sun"),
        ("Baby Shark", "Baby Shark"),
        ('Let It Go - From "Frozen"/Soundtrack Version', "Let It Go"),
        ("Down by the Bay [Live]", "Down by the Bay"),
        ("(I Can't Get No) Satisfaction", "(I Can't Get No) Satisfaction"),
    ],
)
def test_spoken_name(name, spoken):
    assert spoken_name(name) == spoken


async def test_qwen_kind_slot_overrides_the_wording(registry, settings, spotify, policy, fake):
    from coco_hub.intents.engine import Match

    engine = RulesEngine(registry)
    ctx = IntentContext(
        settings=settings, clock=lambda: T0, services={"spotify": spotify, "media_policy": policy}
    )
    request = UtteranceRequest(text="chuck on bananaphone", source="voice", context=IDLE)
    match = Match(
        intent="music.play", slots={"query": "bananaphone", "kind": "song"}, text=request.text
    )
    await engine.dispatch(match, request, ctx, matched_by="qwen")
    assert fake.calls("GET", "/v1/search")[0].url.params["type"] == "track"


def test_music_intents_are_offered_to_qwen(registry):
    for name in ("music.play", "music.now_playing"):
        intent = registry.get(name)
        assert intent.offered_to_llm, name
        assert len(intent.description) > 20
    play = registry.get("music.play")
    # The keys the Qwen spike and hub/eval/utterances.jsonl use.
    assert set(play.llm_slots) == {"query", "kind"}
    assert set(play.llm_slots["kind"].enum) == {"artist", "album", "playlist", "song", "any"}


# --- handlers ----------------------------------------------------------------------------


class SpyPolicy:
    def __init__(self, decide=None) -> None:
        self.requests: list[MediaRequest] = []
        self.decide = decide or (lambda r: MediaDecision.allow())

    def check(self, request: MediaRequest) -> MediaDecision:
        self.requests.append(request)
        return self.decide(request)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def policy() -> SpyPolicy:
    return SpyPolicy()


def _service(conn, settings, fake, *, logged_in=True, **spotify) -> SpotifyService:
    if logged_in:
        log_in(conn, T0)
    return SpotifyService.from_settings(
        conn, spotify_settings(settings, **spotify), clock=lambda: T0, transport=fake.transport
    )


@pytest.fixture
def spotify(conn, settings, fake) -> SpotifyService:
    return _service(conn, settings, fake)


def _runner(registry, settings, services):
    engine = RulesEngine(registry)
    ctx = IntentContext(settings=settings, clock=lambda: T0, services=services)

    async def _run(text: str, context: UtteranceContext = IDLE) -> Action:
        result = await engine.handle(
            UtteranceRequest(text=text, source="voice", context=context), ctx
        )
        assert isinstance(result, Action), result
        return result

    return _run


@pytest.fixture
def run(registry, settings, spotify, policy, conn):
    youtube = YoutubeService(conn, api_key=None)
    return _runner(
        registry, settings, {"spotify": spotify, "media_policy": policy, "youtube": youtube}
    )


async def test_play_artist(run, fake, policy):
    action = await run("play Raffi")
    assert action.intent == "music.play"
    assert action.say == "Playing Raffi on Spotify."
    assert action.view == NowPlayingView()
    (play,) = fake.calls("PUT", "/v1/me/player/play")
    assert play.url.params["device_id"] == IPAD_ID
    assert fake.body(play) == {"context_uri": "spotify:artist:4RaffiArtistId0000000a"}
    assert policy.requests[0] == MediaRequest(kind="music", query="raffi")
    assert policy.requests[1] == MediaRequest(
        kind="music", query="raffi", item_id="spotify:artist:4RaffiArtistId0000000a", title="Raffi"
    )


async def test_play_album_by_kind(run, fake):
    fake.set("GET", "/v1/search", json_response(200, "search_frozen_album"))
    action = await run("play the Frozen soundtrack")
    assert action.say == "Playing Frozen on Spotify."
    search = fake.calls("GET", "/v1/search")[0]
    assert search.url.params["type"] == "album"
    assert search.url.params["q"] == "frozen soundtrack"
    assert fake.body(fake.calls("PUT", "/v1/me/player/play")[0]) == {
        "context_uri": "spotify:album:2FrozenOSTAlbumId0000a"
    }


async def test_play_song_uses_uris(run, fake):
    action = await run("play the song Bananaphone")
    assert fake.calls("GET", "/v1/search")[0].url.params["type"] == "track"
    assert fake.body(fake.calls("PUT", "/v1/me/player/play")[0]) == {
        "uris": ["spotify:track:3BananaPhoneTrackId000a"]
    }
    assert action.say == "Playing Bananaphone on Spotify."


async def test_play_some_music_resumes_on_the_ipad_through_the_policy(run, fake, policy):
    action = await run("play some music")
    assert action.say == "Playing music on Spotify."
    assert action.view == NowPlayingView()
    assert fake.calls("GET", "/v1/search") == []
    (play,) = fake.calls("PUT", "/v1/me/player/play")
    assert fake.body(play) is None and play.url.params["device_id"] == IPAD_ID
    assert policy.requests == [MediaRequest(kind="music", query="some music")]


@pytest.mark.parametrize(
    "text",
    [
        "play Raffi",
        "play some music",
        "play the Frozen soundtrack",
        "put on my Discover Weekly",
        "play Taylor Swift on Spotify",
        "play songs by the Wiggles",
        "play Baby Shark",
    ],
)
async def test_every_play_asks_the_policy_first(run, policy, fake, text):
    policy.decide = lambda r: MediaDecision.deny("Let's ask a grown-up first.")
    action = await run(text)
    assert action.intent == "music.play"
    assert action.say == "Let's ask a grown-up first."
    assert action.view is None
    assert len(policy.requests) == 1 and policy.requests[0].kind == "music"
    assert fake.requests == []


async def test_policy_can_deny_one_item_and_the_next_is_played(run, policy, fake):
    policy.decide = lambda r: (
        MediaDecision.deny("Not that one.")
        if r.item_id == "spotify:artist:4RaffiArtistId0000000a"
        else MediaDecision.allow()
    )
    action = await run("play Raffi")
    # Next best: every query word in the name, then popularity.
    assert action.say == "Playing Raffi Tribute Band on Spotify."
    assert [r.item_id for r in policy.requests] == [
        None,
        "spotify:artist:4RaffiArtistId0000000a",
        "spotify:artist:6RaffiTributeBand0000a",
    ]


async def test_every_item_denied_speaks_the_first_reason(run, policy, fake):
    policy.decide = lambda r: (
        MediaDecision.allow() if r.item_id is None else MediaDecision.deny("Not today.")
    )
    action = await run("play Raffi")
    assert action.say == "Not today."
    assert fake.calls("PUT", "/v1/me/player/play") == []


async def test_no_results(run, fake):
    fake.set("GET", "/v1/search", json_response(200, json={"tracks": {"items": []}}))
    action = await run("play zzqqxx")
    assert action.say == "I couldn't find that on Spotify."
    assert action.view is None


async def test_missing_device(run, fake):
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    action = await run("play Raffi")
    assert action.say == "Open Spotify on the iPad once, then try again."
    assert action.view is None


async def test_premium_required(run, fake):
    fake.set("PUT", "/v1/me/player/play", json_response(403, "error_premium_required"))
    action = await run("play Raffi")
    assert "Premium" in action.say


async def test_unconfigured_and_not_connected(registry, settings, conn, fake, policy):
    unconfigured = SpotifyService.from_settings(conn, settings, transport=fake.transport)
    run = _runner(registry, settings, {"spotify": unconfigured, "media_policy": policy})
    assert (await run("play Raffi")).say == "Spotify isn't set up yet."
    assert (await run("what's playing", MUSIC)).say == "Spotify isn't set up yet."
    not_connected = _service(conn, settings, fake, logged_in=False)
    run = _runner(registry, settings, {"spotify": not_connected, "media_policy": policy})
    assert (await run("play Raffi")).say == "Spotify needs to be connected on the Mac first."
    assert fake.requests == []


async def test_missing_policy_fails_closed(registry, settings, spotify):
    run = _runner(registry, settings, {"spotify": spotify})
    with pytest.raises(KeyError):
        await run("play Raffi")


async def test_whats_playing(run, fake):
    action = await run("what's playing", MUSIC)
    assert action.say == "Nothing's playing on Spotify."
    fake.set("GET", "/v1/me/player/currently-playing", json_response(200, "currently_playing"))
    action = await run("what song is this", MUSIC)
    assert action.say == "This is Bananaphone by Raffi."
    assert action.view == NowPlayingView()


# --- transport + volume reach Spotify when music is playing ------------------------------


@pytest.mark.parametrize(
    ("text", "command", "call"),
    [
        ("pause", "media.pause", ("PUT", "/v1/me/player/pause")),
        ("stop the music", "media.stop", ("PUT", "/v1/me/player/pause")),
        ("next song", "media.next", ("POST", "/v1/me/player/next")),
        ("keep playing", "media.resume", ("PUT", "/v1/me/player/play")),
    ],
)
async def test_controls_call_spotify_for_music(run, fake, policy, text, command, call):
    action = await run(text, MUSIC)
    assert action.commands == [MediaCommand(type=command, target="music")]
    assert action.say is None
    assert call in fake.api_calls()
    assert policy.requests == []


async def test_controls_leave_spotify_alone_for_youtube(run, fake):
    await run("pause", YT)
    await run("volume 5", YT)
    assert fake.requests == []


async def test_controls_with_spotify_not_ready_still_send_display_commands(
    registry, settings, conn, fake, policy
):
    not_connected = _service(conn, settings, fake, logged_in=False)
    run = _runner(registry, settings, {"spotify": not_connected, "media_policy": policy})
    action = await run("pause", MUSIC)
    assert action.commands == [MediaCommand(type="media.pause", target="music")]
    assert action.say is None
    assert fake.requests == []


async def test_control_errors_are_quiet_except_resume(run, fake):
    fake.set("PUT", "/v1/me/player/pause", json_response(500, json={}))
    action = await run("pause", MUSIC)
    assert action.say is None
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    action = await run("resume", MUSIC)
    assert action.say == "Open Spotify on the iPad once, then try again."
    assert action.commands == [MediaCommand(type="media.resume", target="music")]


async def test_volume_reaches_spotify_for_music(run, fake):
    action = await run("volume 7", MUSIC)
    assert action.commands == [VolumeSet(level=70)]
    action = await run("turn it down", MUSIC)
    assert action.commands == [VolumeChange(delta=-15)]
    levels = [r.url.params["volume_percent"] for r in fake.calls("PUT", "/v1/me/player/volume")]
    assert levels == ["70", "25"]


async def test_volume_unsupported_is_spoken(run, fake):
    from fake_spotify import fixture

    data = fixture("devices")
    data["devices"][0]["supports_volume"] = False
    fake.set("GET", "/v1/me/player/devices", json_response(200, json=data))
    action = await run("louder", MUSIC)
    assert action.commands == [VolumeChange(delta=15)]
    assert action.say == "Spotify won't let me change the iPad's volume. Use its volume buttons."
