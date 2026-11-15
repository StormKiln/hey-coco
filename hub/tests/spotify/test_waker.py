"""DisplayWaker: the Hub asks the Display to open Spotify when the iPad isn't listed (#5)."""

from datetime import UTC, datetime

import pytest

from coco_hub import db
from coco_hub.events import EventBus
from coco_hub.media.spotify.errors import SpotifyNoDevice
from coco_hub.media.spotify.service import SpotifyService
from coco_hub.media.spotify.waker import DisplayWaker
from coco_hub.protocol import SpotifyWakeRequestedEvent

from fake_spotify import IPAD_ID, FakeSpotify, json_response, log_in, spotify_settings

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


class Sleeps:
    def __init__(self, on_sleep=None) -> None:
        self.calls: list[float] = []
        self.on_sleep = on_sleep

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep()


async def test_publishes_wake_requested_and_waits():
    bus = EventBus()
    sleeps = Sleeps()
    waker = DisplayWaker(bus, wait_s=4.0, clock=lambda: T0, sleep=sleeps)
    with bus.subscribe() as sub:
        assert await waker.wake("Kitchen iPad") is True
        event = sub.get_nowait()
    assert event == SpotifyWakeRequestedEvent(server_time=T0, device_name="Kitchen iPad")
    assert sleeps.calls == [4.0]


async def test_no_display_connected_does_nothing():
    bus = EventBus()
    sleeps = Sleeps()
    waker = DisplayWaker(bus, wait_s=4.0, clock=lambda: T0, sleep=sleeps)
    assert await waker.wake("Kitchen iPad") is False
    assert sleeps.calls == []


async def test_zero_wait_turns_waking_off():
    bus = EventBus()
    sleeps = Sleeps()
    waker = DisplayWaker(bus, wait_s=0, clock=lambda: T0, sleep=sleeps)
    with bus.subscribe() as sub:
        assert await waker.wake(None) is False
        assert sub.get_nowait() is None
    assert sleeps.calls == []


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


def _service(conn, settings, fake, waker) -> SpotifyService:
    log_in(conn, T0)
    return SpotifyService.from_settings(
        conn, spotify_settings(settings), clock=lambda: T0, transport=fake.transport, waker=waker
    )


async def test_service_relists_after_the_display_opened_spotify(conn, settings):
    fake = FakeSpotify()
    fake.queue("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    bus = EventBus()
    waker = DisplayWaker(bus, wait_s=4.0, clock=lambda: T0, sleep=Sleeps())
    service = _service(conn, settings, fake, waker)
    with bus.subscribe() as sub:
        device = await service.target_device()
        assert isinstance(sub.get_nowait(), SpotifyWakeRequestedEvent)
    assert device.id == IPAD_ID
    assert len(fake.calls("GET", "/v1/me/player/devices")) == 2


async def test_guided_access_display_ignores_it_and_coco_says_open_spotify(conn, settings):
    """Under Guided Access the Display does nothing, so the iPad never appears."""
    fake = FakeSpotify()
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    bus = EventBus()
    waker = DisplayWaker(bus, wait_s=4.0, clock=lambda: T0, sleep=Sleeps())
    service = _service(conn, settings, fake, waker)
    with bus.subscribe():
        with pytest.raises(SpotifyNoDevice) as exc:
            await service.target_device()
    assert exc.value.say == "Open Spotify on the iPad once, then try again."
