import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coco_hub import db
from coco_hub.events import EventBus
from coco_hub.media.spotify.client import Item, parse_devices
from coco_hub.media.spotify.errors import (
    SpotifyNoDevice,
    SpotifyNotConfigured,
    SpotifyNotConnected,
    SpotifyVolumeUnsupported,
)
from coco_hub.media.spotify.service import (
    ACTIVE_POLL_S,
    ACTIVE_WINDOW,
    PLAYING_POLL_S,
    SpotifyService,
    choose_device,
    rank,
)
from coco_hub.protocol import NowPlayingEvent
from coco_hub.settings import Tls

from fake_spotify import (
    IPAD_ID,
    FakeSpotify,
    fixture,
    json_response,
    log_in,
    spotify_settings,
)

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class SpyWaker:
    def __init__(self, fake: FakeSpotify | None = None, result: bool = True) -> None:
        self.calls: list[str | None] = []
        self.fake = fake
        self.result = result

    async def wake(self, device_name: str | None, appeared) -> bool:
        self.calls.append(device_name)
        if self.fake is not None:  # the iPad shows up after waking
            self.fake.set("GET", "/v1/me/player/devices", json_response(200, "devices"))
        return self.result and await appeared()


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def make(conn, settings, clock, fake, bus):
    def _make(*, logged_in: bool = True, waker=None, **spotify) -> SpotifyService:
        if logged_in:
            log_in(conn, T0)
        service = SpotifyService.from_settings(
            conn,
            spotify_settings(settings, **spotify),
            clock=clock,
            bus=bus,
            transport=fake.transport,
            waker=waker,
        )
        return service

    return _make


@pytest.fixture
def service(make) -> SpotifyService:
    return make()


# --- readiness ---------------------------------------------------------------------------


async def test_unconfigured_is_inert(conn, settings, clock, fake):
    service = SpotifyService.from_settings(conn, settings, clock=clock, transport=fake.transport)
    assert not service.configured and not service.ready
    with pytest.raises(SpotifyNotConfigured) as info:
        service.ensure_ready()
    assert info.value.say == "Spotify isn't set up yet."
    assert service.next_poll_delay() is None
    await service.poll_once()
    assert fake.requests == []


async def test_not_logged_in(make, fake):
    service = make(logged_in=False)
    assert service.configured and not service.ready
    with pytest.raises(SpotifyNotConnected) as info:
        service.ensure_ready()
    assert info.value.say == "Spotify needs to be connected on the Mac first."
    await service.poll_once()
    assert fake.requests == []


def test_default_redirect_uri_is_a_loopback_ip_over_https(make):
    # The Hub serves TLS on its one port (#54), so the browser comes back over https.
    service = make(logged_in=False)
    assert service.auth.redirect_uri == "https://127.0.0.1:8765/spotify/callback"
    custom = make(logged_in=False, redirect_uri="https://127.0.0.1:9000/spotify/callback")
    assert custom.auth.redirect_uri == "https://127.0.0.1:9000/spotify/callback"


def test_default_redirect_uri_is_http_without_tls(conn, settings, clock):
    plain = spotify_settings(settings).model_copy(update={"tls": Tls(enabled=False)})
    service = SpotifyService.from_settings(conn, plain, clock=clock)
    assert service.auth.redirect_uri == "http://127.0.0.1:8765/spotify/callback"


# --- devices -----------------------------------------------------------------------------


def test_choose_device_by_name_case_insensitively():
    devices = parse_devices(fixture("devices"))
    assert choose_device(devices, "kitchen ipad").id == IPAD_ID
    assert choose_device(devices, " Kitchen iPad ").id == IPAD_ID
    assert choose_device(devices, "Bedroom iPad") is None


def test_choose_device_without_a_name_prefers_the_active_one():
    devices = parse_devices(fixture("devices"))
    assert choose_device(devices, None).name == "Office MacBook Pro"
    only = parse_devices(fixture("devices_no_ipad"))
    assert choose_device(only, None).name == "Office MacBook Pro"
    inactive = [devices[0], devices[1].__class__(**{**devices[1].__dict__, "is_active": False})]
    assert choose_device(inactive, None) is None
    assert choose_device([], None) is None


async def test_play_targets_the_named_device(service, fake):
    item = Item(kind="artist", uri="spotify:artist:4RaffiArtistId0000000a", name="Raffi")
    await service.play(item)
    (request,) = fake.calls("PUT", "/v1/me/player/play")
    assert request.url.params["device_id"] == IPAD_ID
    assert fake.body(request) == {"context_uri": "spotify:artist:4RaffiArtistId0000000a"}


async def test_play_track_and_resume(service, fake):
    track = Item(kind="track", uri="spotify:track:3BananaPhoneTrackId000a", name="Bananaphone")
    await service.play(track)
    await service.play(None)
    first, second = fake.calls("PUT", "/v1/me/player/play")
    assert fake.body(first) == {"uris": ["spotify:track:3BananaPhoneTrackId000a"]}
    assert fake.body(second) is None
    assert second.url.params["device_id"] == IPAD_ID


async def test_missing_device_is_spoken_and_waker_is_tried(make, fake):
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    waker = SpyWaker(result=False)
    service = make(waker=waker)
    with pytest.raises(SpotifyNoDevice) as info:
        await service.play(None)
    assert info.value.say == "Open Spotify on the iPad once, then try again."
    assert waker.calls == ["Kitchen iPad"]
    assert fake.calls("PUT", "/v1/me/player/play") == []


async def test_waker_that_succeeds_lets_play_continue(make, fake):
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    waker = SpyWaker(fake)
    service = make(waker=waker)
    await service.play(None)
    assert waker.calls == ["Kitchen iPad"]
    (request,) = fake.calls("PUT", "/v1/me/player/play")
    assert request.url.params["device_id"] == IPAD_ID


async def test_default_waker_is_a_no_op(service, fake):
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    with pytest.raises(SpotifyNoDevice):
        await service.play(None)
    assert len(fake.calls("GET", "/v1/me/player/devices")) == 1


# --- search + ranking --------------------------------------------------------------------


def test_rank_prefers_exact_name_then_popularity():
    items = [
        Item(kind="artist", uri="a:1", name="Raffaella Carrà", popularity=66),
        Item(kind="artist", uri="a:2", name="Raffi", popularity=58),
        Item(kind="track", uri="t:1", name="Bananaphone", artist="Raffi", popularity=55),
        Item(kind="playlist", uri="p:1", name="Raffi Radio", artist="Spotify"),
    ]
    assert [i.uri for i in rank(items, "raffi")] == ["a:2", "p:1", "a:1", "t:1"]


def test_rank_ignores_case_punctuation_and_a_leading_the():
    items = [
        Item(kind="track", uri="t:1", name="Wiggly Party", popularity=40),
        Item(kind="artist", uri="a:1", name="The Wiggles", popularity=60),
    ]
    assert rank(items, "wiggles")[0].uri == "a:1"
    items = [
        Item(kind="playlist", uri="p:1", name="Baby Shark!", popularity=None),
        Item(kind="track", uri="t:1", name="Baby Shark", popularity=70),
    ]
    assert rank(items, "baby shark")[0].uri == "t:1"


def test_rank_all_words_present_beats_more_popular_partial_match():
    items = [
        Item(kind="album", uri="b:1", name="Frozen Lullabies", popularity=30),
        Item(kind="album", uri="b:2", name="Frozen (Original Motion Picture Soundtrack)"),
    ]
    assert rank(items, "frozen soundtrack")[0].uri == "b:2"


async def test_search_any_asks_for_every_kind(service, fake):
    items = await service.search("raffi", "any")
    assert items[0].name == "Raffi"
    (request,) = fake.calls("GET", "/v1/search")
    assert set(request.url.params["type"].split(",")) == {"artist", "album", "playlist", "track"}


async def test_search_one_kind(service, fake):
    fake.set("GET", "/v1/search", json_response(200, "search_frozen_album"))
    items = await service.search("frozen soundtrack", "album")
    assert items[0].uri == "spotify:album:2FrozenOSTAlbumId0000a"
    assert fake.calls("GET", "/v1/search")[0].url.params["type"] == "album"


async def test_search_song_means_track(service, fake):
    await service.search("baby shark", "song")
    assert fake.calls("GET", "/v1/search")[0].url.params["type"] == "track"


# --- transport + volume ------------------------------------------------------------------


async def test_controls(service, fake):
    await service.pause()
    await service.skip()
    await service.resume()
    await service.stop()
    assert fake.api_calls() == [
        ("PUT", "/v1/me/player/pause"),
        ("POST", "/v1/me/player/next"),
        ("GET", "/v1/me/player/devices"),
        ("PUT", "/v1/me/player/play"),
        ("PUT", "/v1/me/player/pause"),
    ]


async def test_volume_set_and_change(service, fake):
    await service.set_volume(70)
    await service.change_volume(-15)  # iPad is at 40 in the fixture
    await service.change_volume(+90)
    levels = [r.url.params["volume_percent"] for r in fake.calls("PUT", "/v1/me/player/volume")]
    assert levels == ["70", "25", "100"]
    assert {r.url.params["device_id"] for r in fake.calls("PUT", "/v1/me/player/volume")} == {
        IPAD_ID
    }


async def test_volume_unsupported_device(make, fake):
    data = fixture("devices")
    data["devices"][0]["supports_volume"] = False
    fake.set("GET", "/v1/me/player/devices", json_response(200, json=data))
    service = make()
    with pytest.raises(SpotifyVolumeUnsupported):
        await service.set_volume(50)
    assert fake.calls("PUT", "/v1/me/player/volume") == []


# --- now playing -------------------------------------------------------------------------


async def test_poll_publishes_only_on_change(service, fake, bus, clock):
    with bus.subscribe() as sub:
        await service.poll_once()  # 204: nothing playing, same as the initial state
        assert sub.get_nowait() is None
        fake.set("GET", "/v1/me/player/currently-playing", json_response(200, "currently_playing"))
        await service.poll_once()
        event = sub.get_nowait()
        assert isinstance(event, NowPlayingEvent)
        assert event.server_time == T0
        assert event.now_playing.title == "Bananaphone"
        assert service.now_playing == event.now_playing
        await service.poll_once()
        assert sub.get_nowait() is None
        paused = fixture("currently_playing")
        paused["is_playing"] = False
        fake.set("GET", "/v1/me/player/currently-playing", json_response(200, json=paused))
        await service.poll_once()
        assert sub.get_nowait().now_playing.is_playing is False
        fake.set("GET", "/v1/me/player/currently-playing", json_response(204))
        await service.poll_once()
        assert sub.get_nowait().now_playing is None


async def test_poll_errors_keep_the_last_state(service, fake, bus):
    fake.set("GET", "/v1/me/player/currently-playing", json_response(200, "currently_playing"))
    await service.poll_once()
    fake.set("GET", "/v1/me/player/currently-playing", json_response(500, json={}))
    with bus.subscribe() as sub:
        await service.poll_once()
        assert sub.get_nowait() is None
    assert service.now_playing.title == "Bananaphone"


async def test_poll_rate_limited_backs_off(service, fake):
    fake.set(
        "GET",
        "/v1/me/player/currently-playing",
        json_response(429, "error_rate_limited", headers={"Retry-After": "90"}),
    )
    await service.poll_once()
    assert service.next_poll_delay() == 90


def test_poll_schedule(service, clock):
    # Nothing played yet and nothing playing: wait for a command.
    assert service.next_poll_delay() is None
    service.touch()
    assert service.next_poll_delay() == ACTIVE_POLL_S == 5
    clock.now = T0 + ACTIVE_WINDOW - timedelta(seconds=1)
    assert service.next_poll_delay() == ACTIVE_POLL_S
    clock.now = T0 + ACTIVE_WINDOW + timedelta(seconds=1)
    assert service.next_poll_delay() is None
    service._now_playing = fixture_now_playing(is_playing=True)
    assert service.next_poll_delay() == PLAYING_POLL_S == 60
    service._now_playing = fixture_now_playing(is_playing=False)
    assert service.next_poll_delay() is None


def fixture_now_playing(*, is_playing: bool):
    from coco_hub.media.spotify.client import parse_currently_playing

    data = fixture("currently_playing")
    data["is_playing"] = is_playing
    return parse_currently_playing(data)


async def test_run_polls_on_start_and_when_touched(service, fake):
    task = asyncio.create_task(service.run())
    try:
        for _ in range(50):
            await asyncio.sleep(0)
        assert len(fake.calls("GET", "/v1/me/player/currently-playing")) == 1
        service.touch()
        for _ in range(50):
            await asyncio.sleep(0)
        assert len(fake.calls("GET", "/v1/me/player/currently-playing")) == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await service.aclose()
