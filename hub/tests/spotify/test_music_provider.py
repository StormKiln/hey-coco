"""music.provider switch (#42): the same phrases go to Spotify or to the Display's Apple Music."""

from datetime import UTC, datetime

import pytest

from coco_hub import db
from coco_hub.intents import build_registry
from coco_hub.intents.engine import IntentContext, Match, RulesEngine
from coco_hub.media.policy import MediaDecision, MediaRequest
from coco_hub.media.spotify.service import SpotifyService
from coco_hub.protocol import (
    Action,
    MediaCommand,
    MusicPlay,
    NowPlayingView,
    UtteranceContext,
    UtteranceRequest,
    VolumeSet,
)
from coco_hub.settings import Settings

from fake_spotify import FakeSpotify, log_in, spotify_settings

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
IDLE = UtteranceContext(view="idle", alarm_ringing=False, media_playing=None)
MUSIC = UtteranceContext(view="now_playing", alarm_ringing=False, media_playing="music")


class SpyPolicy:
    def __init__(self) -> None:
        self.requests: list[MediaRequest] = []
        self.deny_with: str | None = None

    def check(self, request: MediaRequest) -> MediaDecision:
        self.requests.append(request)
        if self.deny_with is not None:
            return MediaDecision.deny(self.deny_with)
        return MediaDecision.allow()


def test_default_provider_is_spotify(settings):
    assert settings.music.provider == "spotify"


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("COCO_MUSIC__PROVIDER", "apple_music")
    assert Settings(token="test-token-0123456789abcdef").music.provider == "apple_music"


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


def _with_provider(settings: Settings, provider: str) -> Settings:
    settings = spotify_settings(settings)
    return settings.model_copy(
        update={"music": settings.music.model_copy(update={"provider": provider})}
    )


@pytest.fixture
def make_run(conn, settings, fake, policy):
    log_in(conn, T0)

    def _make(provider: str):
        s = _with_provider(settings, provider)
        spotify = SpotifyService.from_settings(conn, s, clock=lambda: T0, transport=fake.transport)
        engine = RulesEngine(build_registry())
        ctx = IntentContext(
            settings=s, clock=lambda: T0, services={"spotify": spotify, "media_policy": policy}
        )

        async def run(text: str, context: UtteranceContext = IDLE) -> Action:
            result = await engine.handle(
                UtteranceRequest(text=text, source="voice", context=context), ctx
            )
            assert isinstance(result, Action), result
            return result

        async def dispatch(match: Match) -> Action:
            request = UtteranceRequest(text=match.text, source="voice", context=IDLE)
            return await engine.dispatch(match, request, ctx, matched_by="qwen")

        run.dispatch = dispatch
        return run

    return _make


async def test_spotify_provider_plays_on_spotify(make_run, fake, policy):
    action = await make_run("spotify")("play Raffi")
    assert action.say == "Playing Raffi on Spotify."
    assert action.commands == []
    assert len(fake.calls("PUT", "/v1/me/player/play")) == 1
    assert policy.requests[0] == MediaRequest(kind="music", query="raffi")


@pytest.mark.parametrize(
    ("text", "query", "kind", "say"),
    [
        ("play Raffi", "raffi", "any", "Raffi"),
        ("play songs by the Wiggles", "the wiggles", "artist", "The Wiggles"),
        ("play the Frozen soundtrack", "frozen soundtrack", "album", "Frozen Soundtrack"),
        ("put on my dinner playlist", "dinner", "playlist", "Dinner"),
        ("play the song Let It Go", "let it go", "song", "Let It Go"),
        ("play Taylor Swift on Spotify", "taylor swift", "any", "Taylor Swift"),
        ("play some music", "", "any", "music"),
    ],
)  # fmt: skip
async def test_apple_music_provider_sends_music_play(
    make_run, fake, policy, text, query, kind, say
):
    action = await make_run("apple_music")(text)
    assert action.intent == "music.play"
    assert action.say == f"Playing {say} on Apple Music."
    assert action.view == NowPlayingView()
    assert action.commands == [MusicPlay(query=query, kind=kind)]
    assert fake.requests == []  # never touches Spotify
    assert len(policy.requests) == 1
    assert policy.requests[0].kind == "music"


async def test_apple_music_asks_the_policy_before_sending(make_run, fake, policy):
    policy.deny_with = "Let's ask a grown-up first."
    action = await make_run("apple_music")("play Raffi")
    assert action.say == "Let's ask a grown-up first."
    assert action.commands == []
    assert action.view is None
    assert policy.requests == [MediaRequest(kind="music", query="raffi")]


async def test_apple_music_takes_the_qwen_kind(make_run):
    run = make_run("apple_music")
    match = Match(intent="music.play", slots={"query": "bananaphone", "kind": "song"}, text="x")
    action = await run.dispatch(match)
    assert action.commands == [MusicPlay(query="bananaphone", kind="song")]


async def test_apple_music_whats_playing_shows_the_screen(make_run, fake):
    action = await make_run("apple_music")("what's playing", MUSIC)
    assert action.view == NowPlayingView()
    assert fake.requests == []


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("pause", "media.pause"),
        ("next song", "media.next"),
        ("stop the music", "media.stop"),
        ("keep playing", "media.resume"),
    ],
)
async def test_apple_music_transport_goes_to_the_display_only(make_run, fake, text, command):
    action = await make_run("apple_music")(text, MUSIC)
    assert action.commands == [MediaCommand(type=command, target="music")]
    assert action.say is None
    assert fake.requests == []


async def test_apple_music_volume_goes_to_the_display_only(make_run, fake):
    action = await make_run("apple_music")("volume 5", MUSIC)
    assert action.commands == [VolumeSet(level=50)]
    assert fake.requests == []


async def test_spotify_transport_still_drives_spotify(make_run, fake):
    action = await make_run("spotify")("pause", MUSIC)
    assert action.commands == [MediaCommand(type="media.pause", target="music")]
    assert fake.calls("PUT", "/v1/me/player/pause")
