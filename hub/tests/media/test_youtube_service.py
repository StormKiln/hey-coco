import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from coco_hub import db
from coco_hub.media.youtube import (
    SEARCH_COST,
    USER_AGENT,
    Video,
    YoutubeClient,
    YoutubeService,
    YoutubeUnavailable,
    parse_search,
    quota_day,
    spoken_title,
)
from coco_hub.settings import Settings

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "youtube"
SEARCH = json.loads((FIXTURES / "search_bluey.json").read_text())
QUOTA_EXCEEDED = json.loads((FIXTURES / "quota_exceeded.json").read_text())
# 2026-11-14 10:00 Pacific.
T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Recorder:
    """httpx MockTransport handler that records requests and replays a canned response."""

    def __init__(self, status: int = 200, body: object = SEARCH) -> None:
        self.status = status
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_service(conn, clock, recorder, *, key="test-key", budget=9000) -> YoutubeService:
    return YoutubeService(
        conn,
        api_key=key,
        region="CA",
        daily_budget=budget,
        clock=clock,
        transport=httpx.MockTransport(recorder),
    )


# --- parsing ---------------------------------------------------------------------------


def test_parse_search_decodes_entities_and_skips_upcoming():
    assert parse_search(SEARCH) == [
        Video(
            "a1B2c3D4e5F",
            "Bluey Full Episodes | Season 3 | Rug Island, Dad Baby & more | Bluey",
            "Bluey - Official Channel",
        ),
        Video("Zz9Yy8Xx7Ww", "Bluey's Big Play", "Bluey - Official Channel"),
        Video("Kk1Ll2Mm3Nn", '"Keepy Uppy" - Bluey Song Sing-Along', "Kids Songs & Stories"),
    ]


def test_parse_search_ignores_non_video_items():
    data = {
        "items": [
            {"id": {"kind": "youtube#channel", "channelId": "UC1"}, "snippet": {"title": "x"}},
            {"id": {"kind": "youtube#video"}, "snippet": {"title": "no id"}},
        ]
    }
    assert parse_search(data) == []


@pytest.mark.parametrize(
    ("title", "spoken"),
    [
        ("Bluey's Big Play", "Bluey's Big Play"),
        (
            "Bluey Full Episodes | Season 3 | Rug Island, Dad Baby & more | Bluey",
            "Bluey Full Episodes",
        ),
        (
            "Monster Trucks for Kids - Big Trucks Crushing Cars Compilation 2024 #trucks",
            "Monster Trucks for Kids",
        ),
        (
            "A very long title without any separators at all that keeps going and going on",
            "A very long title without any separators at all that keeps",
        ),
    ],
)
def test_spoken_title(title, spoken):
    assert spoken_title(title) == spoken


# --- client ----------------------------------------------------------------------------


async def test_client_sends_expected_query():
    recorder = Recorder()
    client = YoutubeClient("test-key", region="CA", transport=httpx.MockTransport(recorder))
    try:
        videos = await client.search("bluey")
    finally:
        await client.aclose()
    assert len(videos) == 3
    (request,) = recorder.requests
    assert request.url.host == "www.googleapis.com"
    assert request.url.path == "/youtube/v3/search"
    assert dict(request.url.params) == {
        "part": "snippet",
        "type": "video",
        "videoEmbeddable": "true",
        "safeSearch": "moderate",
        "maxResults": "5",
        "regionCode": "CA",
        "q": "bluey",
        "key": "test-key",
    }
    assert request.headers["User-Agent"] == USER_AGENT


# --- service: key, quota, cache --------------------------------------------------------


async def test_missing_key_is_spoken(conn, clock):
    recorder = Recorder()
    service = make_service(conn, clock, recorder, key=None)
    with pytest.raises(YoutubeUnavailable) as exc:
        await service.search("bluey")
    assert exc.value.say == "YouTube isn't set up yet."
    assert recorder.requests == []


def test_service_from_settings_reads_key_and_region(conn, clock, tmp_path):
    settings = Settings(
        token="test-token-0123456789abcdef",
        data_dir=tmp_path,
        keys={"youtube": "k"},
        youtube={"region": "US", "daily_quota": 500},
    )
    service = YoutubeService.from_settings(conn, settings, clock=clock)
    assert service.configured
    assert service.region == "US"
    assert service.daily_budget == 500


def test_youtube_settings_defaults(tmp_path):
    settings = Settings(token="test-token-0123456789abcdef", data_dir=tmp_path)
    assert settings.youtube.region == "CA"
    assert settings.youtube.daily_quota == 9000
    assert settings.keys.youtube is None


async def test_search_spends_quota(conn, clock):
    service = make_service(conn, clock, Recorder())
    assert service.quota_used() == 0
    await service.search("bluey")
    assert service.quota_used() == SEARCH_COST
    await service.search("paw patrol")
    assert service.quota_used() == 2 * SEARCH_COST


async def test_cache_hit_avoids_quota_and_network(conn, clock):
    recorder = Recorder()
    service = make_service(conn, clock, recorder)
    first = await service.search("bluey")
    clock.now += timedelta(hours=12)  # same quota day
    assert await service.search("Bluey ") == first
    assert len(recorder.requests) == 1
    assert service.quota_used() == SEARCH_COST


async def test_cache_survives_a_new_service(conn, clock):
    await make_service(conn, clock, Recorder()).search("bluey")
    recorder = Recorder()
    assert len(await make_service(conn, clock, recorder).search("bluey")) == 3
    assert recorder.requests == []


async def test_cache_expires_after_24_hours(conn, clock):
    recorder = Recorder()
    service = make_service(conn, clock, recorder)
    await service.search("bluey")
    clock.now += timedelta(hours=24, seconds=1)
    await service.search("bluey")
    assert len(recorder.requests) == 2


async def test_quota_refuses_past_budget(conn, clock):
    recorder = Recorder()
    service = make_service(conn, clock, recorder, budget=2 * SEARCH_COST)
    await service.search("one")
    await service.search("two")
    with pytest.raises(YoutubeUnavailable) as exc:
        await service.search("three")
    assert "YouTube" in exc.value.say and "tomorrow" in exc.value.say
    assert len(recorder.requests) == 2
    # Cached queries still work when the budget is spent.
    assert len(await service.search("one")) == 3


async def test_quota_resets_at_midnight_pacific(conn, clock):
    recorder = Recorder()
    service = make_service(conn, clock, recorder, budget=SEARCH_COST)
    clock.now = datetime(2026, 11, 15, 7, 59, tzinfo=UTC)  # 23:59 Pacific, Nov 14
    await service.search("one")
    with pytest.raises(YoutubeUnavailable):
        await service.search("two")
    clock.now = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)  # 00:00 Pacific, Nov 15
    assert service.quota_used() == 0
    await service.search("two")
    assert service.quota_used() == SEARCH_COST


async def test_quota_persists_across_services(conn, clock):
    await make_service(conn, clock, Recorder()).search("one")
    assert make_service(conn, clock, Recorder()).quota_used() == SEARCH_COST


def test_quota_day_uses_pacific_time():
    assert quota_day(datetime(2026, 11, 15, 7, 59, tzinfo=UTC)) == "2026-11-14"
    assert quota_day(datetime(2026, 11, 15, 8, 0, tzinfo=UTC)) == "2026-11-15"
    # Daylight time: midnight PDT is 07:00 UTC.
    assert quota_day(datetime(2026, 7, 1, 7, 0, tzinfo=UTC)) == "2026-07-01"


async def test_youtube_quota_error_marks_the_day_exhausted(conn, clock):
    recorder = Recorder(status=403, body=QUOTA_EXCEEDED)
    service = make_service(conn, clock, recorder)
    with pytest.raises(YoutubeUnavailable) as exc:
        await service.search("bluey")
    assert "tomorrow" in exc.value.say
    with pytest.raises(YoutubeUnavailable):
        await service.search("paw patrol")
    assert len(recorder.requests) == 1


async def test_fetch_failure_is_spoken_and_not_cached(conn, clock):
    recorder = Recorder(status=500, body={"error": {"code": 500}})
    service = make_service(conn, clock, recorder)
    with pytest.raises(YoutubeUnavailable) as exc:
        await service.search("bluey")
    assert exc.value.say == "I couldn't reach YouTube."
    recorder.status, recorder.body = 200, SEARCH
    assert len(await service.search("bluey")) == 3


async def test_network_error_is_spoken(conn, clock):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    service = YoutubeService(
        conn,
        api_key="k",
        region="CA",
        daily_budget=9000,
        clock=clock,
        transport=httpx.MockTransport(boom),
    )
    with pytest.raises(YoutubeUnavailable) as exc:
        await service.search("bluey")
    assert exc.value.say == "I couldn't reach YouTube."
