import pytest

from coco_hub.media.spotify.client import (
    Device,
    Item,
    SpotifyClient,
    parse_currently_playing,
    parse_devices,
    parse_search,
)
from coco_hub.media.spotify.errors import (
    SpotifyError,
    SpotifyNoDevice,
    SpotifyPremiumRequired,
    SpotifyRateLimited,
)
from coco_hub.protocol import NowPlaying

from fake_spotify import IPAD_ID, FakeSpotify, fixture, json_response

pytestmark = pytest.mark.anyio


class FakeTokens:
    def __init__(self) -> None:
        self.token = "BQFirst"
        self.refreshes = 0

    async def access_token(self) -> str:
        return self.token

    async def refresh(self, stale: str | None = None) -> str:
        self.refreshes += 1
        self.token = f"BQRefreshed{self.refreshes}"
        return self.token


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def tokens() -> FakeTokens:
    return FakeTokens()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
async def client(fake, tokens, sleeps):
    c = SpotifyClient(tokens, transport=fake.transport, sleep=sleeps)
    yield c
    await c.aclose()


# --- parsing -----------------------------------------------------------------------------


def test_parse_search_skips_null_items_and_reads_every_kind():
    items = parse_search(fixture("search_raffi"))
    assert [i.kind for i in items] == ["artist"] * 3 + ["track", "album", "playlist"]
    raffi = items[1]
    assert raffi == Item(
        kind="artist",
        uri="spotify:artist:4RaffiArtistId0000000a",
        name="Raffi",
        artist=None,
        popularity=58,
    )
    track = items[3]
    assert (track.name, track.artist, track.popularity) == ("Bananaphone", "Raffi", 55)
    playlist = items[-1]
    assert (playlist.name, playlist.artist, playlist.popularity) == ("Raffi Radio", "Spotify", None)


def test_parse_search_tolerates_missing_sections():
    assert parse_search({}) == []
    assert parse_search({"tracks": {"items": [None, {"type": "track"}]}}) == []


def test_parse_devices_drops_restricted_devices_without_an_id():
    devices = parse_devices(fixture("devices"))
    assert devices == [
        Device(
            id=IPAD_ID,
            name="Kitchen iPad",
            type="Tablet",
            is_active=False,
            volume_percent=40,
            supports_volume=True,
        ),
        Device(
            id="9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
            name="Office MacBook Pro",
            type="Computer",
            is_active=True,
            volume_percent=75,
            supports_volume=True,
        ),
    ]


def test_parse_currently_playing():
    assert parse_currently_playing(fixture("currently_playing")) == NowPlaying(
        source="spotify",
        title="Bananaphone",
        artist="Raffi",
        album="Bananaphone",
        artwork_url="https://i.scdn.co/image/ab67616d0000b273bananaphone640",
        is_playing=True,
    )
    assert parse_currently_playing({"is_playing": False, "item": None}) is None


# --- requests ----------------------------------------------------------------------------


async def test_search_sends_query_types_and_bearer(client, fake):
    items = await client.search("raffi", ["artist", "track"])
    assert items
    (request,) = fake.calls("GET", "/v1/search")
    assert request.url.host == "api.spotify.com"
    assert request.url.params["q"] == "raffi"
    assert request.url.params["type"] == "artist,track"
    assert request.headers["authorization"] == "Bearer BQFirst"


async def test_play_context_on_a_device(client, fake):
    await client.play(IPAD_ID, context_uri="spotify:artist:4RaffiArtistId0000000a")
    (request,) = fake.calls("PUT", "/v1/me/player/play")
    assert request.url.params["device_id"] == IPAD_ID
    assert fake.body(request) == {"context_uri": "spotify:artist:4RaffiArtistId0000000a"}


async def test_play_track_uses_uris_and_resume_sends_no_body(client, fake):
    await client.play(IPAD_ID, uris=["spotify:track:3BananaPhoneTrackId000a"])
    await client.play(IPAD_ID)
    first, second = fake.calls("PUT", "/v1/me/player/play")
    assert fake.body(first) == {"uris": ["spotify:track:3BananaPhoneTrackId000a"]}
    assert fake.body(second) is None


async def test_transport_and_volume(client, fake):
    await client.pause()
    await client.next()
    await client.set_volume(55, IPAD_ID)
    assert fake.api_calls() == [
        ("PUT", "/v1/me/player/pause"),
        ("POST", "/v1/me/player/next"),
        ("PUT", "/v1/me/player/volume"),
    ]
    volume = fake.calls("PUT", "/v1/me/player/volume")[0]
    assert volume.url.params["volume_percent"] == "55"
    assert volume.url.params["device_id"] == IPAD_ID


async def test_currently_playing_204_is_none(client, fake):
    assert await client.currently_playing() is None
    fake.set("GET", "/v1/me/player/currently-playing", json_response(200, "currently_playing"))
    assert (await client.currently_playing()).title == "Bananaphone"


# --- errors ------------------------------------------------------------------------------


async def test_401_refreshes_once_and_retries(client, fake, tokens):
    fake.queue("GET", "/v1/me/player/devices", json_response(401, "error_expired_token"))
    devices = await client.devices()
    assert [d.name for d in devices] == ["Kitchen iPad", "Office MacBook Pro"]
    assert tokens.refreshes == 1
    first, second = fake.calls("GET", "/v1/me/player/devices")
    assert first.headers["authorization"] == "Bearer BQFirst"
    assert second.headers["authorization"] == "Bearer BQRefreshed1"


async def test_401_twice_is_an_error(client, fake, tokens):
    fake.set("GET", "/v1/me/player/devices", json_response(401, "error_expired_token"))
    with pytest.raises(SpotifyError):
        await client.devices()
    assert tokens.refreshes == 1


async def test_404_no_active_device(client, fake):
    fake.queue("PUT", "/v1/me/player/pause", json_response(404, "error_no_active_device"))
    with pytest.raises(SpotifyNoDevice) as info:
        await client.pause()
    assert info.value.say == "Open Spotify on the iPad once, then try again."


async def test_403_premium_required(client, fake):
    fake.queue("PUT", "/v1/me/player/play", json_response(403, "error_premium_required"))
    with pytest.raises(SpotifyPremiumRequired):
        await client.play(IPAD_ID)


async def test_other_403_is_a_plain_error(client, fake):
    fake.queue(
        "PUT",
        "/v1/me/player/pause",
        json_response(
            403,
            json={"error": {"status": 403, "message": "Restriction violated", "reason": "UNKNOWN"}},
        ),
    )
    with pytest.raises(SpotifyError) as info:
        await client.pause()
    assert not isinstance(info.value, SpotifyPremiumRequired)


async def test_429_short_retry_after_waits_and_retries(client, fake, sleeps):
    fake.queue(
        "GET",
        "/v1/search",
        json_response(429, "error_rate_limited", headers={"Retry-After": "2"}),
    )
    assert await client.search("raffi", ["artist"])
    assert sleeps.calls == [2.0]
    assert len(fake.calls("GET", "/v1/search")) == 2


async def test_429_long_retry_after_gives_up_with_the_wait(client, fake, sleeps):
    fake.set(
        "GET",
        "/v1/search",
        json_response(429, "error_rate_limited", headers={"Retry-After": "120"}),
    )
    with pytest.raises(SpotifyRateLimited) as info:
        await client.search("raffi", ["artist"])
    assert info.value.retry_after == 120
    assert sleeps.calls == []
    assert "busy" in info.value.say


async def test_429_twice_gives_up(client, fake, sleeps):
    fake.set(
        "GET", "/v1/search", json_response(429, "error_rate_limited", headers={"Retry-After": "1"})
    )
    with pytest.raises(SpotifyRateLimited):
        await client.search("raffi", ["artist"])
    assert sleeps.calls == [1.0]


async def test_server_error_and_unreadable_body(client, fake):
    fake.queue("GET", "/v1/search", json_response(502, json={}))
    with pytest.raises(SpotifyError):
        await client.search("raffi", ["artist"])
    fake.queue("GET", "/v1/search", json_response(200, content=b"<html>"))
    with pytest.raises(SpotifyError):
        await client.search("raffi", ["artist"])
