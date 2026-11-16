"""DisplayWaker: the Hub asks the Display to open Spotify when the iPad isn't listed (#5),
then polls the device list until it shows up (#95)."""

from datetime import UTC, datetime, timedelta

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


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Sleeps:
    """Fake ``asyncio.sleep``: records the delays and moves ``clock`` on by them."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.calls: list[float] = []
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.now += timedelta(seconds=seconds)


class AppearsAfter:
    """``appeared`` callback: False for the first ``n`` checks, then True."""

    def __init__(self, n: int | None) -> None:
        self.n = n
        self.checks = 0

    async def __call__(self) -> bool:
        self.checks += 1
        return self.n is not None and self.checks > self.n


def make_waker(bus, *, wait_s=12.0, poll_s=1.0, clock=None, sleeps=None) -> DisplayWaker:
    clock = clock or Clock()
    return DisplayWaker(
        bus, wait_s=wait_s, poll_s=poll_s, clock=clock, sleep=sleeps or Sleeps(clock)
    )


async def test_publishes_wake_requested_then_polls_until_it_appears():
    bus = EventBus()
    clock = Clock()
    sleeps = Sleeps(clock)
    waker = make_waker(bus, clock=clock, sleeps=sleeps)
    appeared = AppearsAfter(5)  # listed on the 6th check, ~6 s after: as in the kitchen
    with bus.subscribe() as sub:
        assert await waker.wake("iPad", appeared) is True
        event = sub.get_nowait()
    assert event == SpotifyWakeRequestedEvent(server_time=T0, device_name="iPad")
    assert sleeps.calls == [1.0] * 6
    assert appeared.checks == 6


async def test_gives_up_after_wait_s():
    bus = EventBus()
    clock = Clock()
    sleeps = Sleeps(clock)
    waker = make_waker(bus, wait_s=12.0, poll_s=1.0, clock=clock, sleeps=sleeps)
    appeared = AppearsAfter(None)
    with bus.subscribe():
        assert await waker.wake("iPad", appeared) is False
    assert sum(sleeps.calls) == pytest.approx(12.0)
    assert appeared.checks == 12


async def test_slow_device_listings_count_towards_the_wait():
    """Wall-clock time spent listing devices counts: the clock is checked, not just sleeps."""
    bus = EventBus()
    clock = Clock()
    sleeps = Sleeps(clock)
    waker = make_waker(bus, wait_s=12.0, poll_s=1.0, clock=clock, sleeps=sleeps)

    async def slow_never() -> bool:
        clock.now += timedelta(seconds=2)  # each listing takes 2 s
        return False

    with bus.subscribe():
        assert await waker.wake("iPad", slow_never) is False
    assert len(sleeps.calls) == 4  # 4 x (1 s sleep + 2 s listing) = 12 s


async def test_last_poll_is_cut_to_the_deadline():
    bus = EventBus()
    sleeps = Sleeps(Clock())
    waker = make_waker(bus, wait_s=2.5, poll_s=1.0, sleeps=sleeps, clock=sleeps.clock)
    with bus.subscribe():
        assert await waker.wake(None, AppearsAfter(None)) is False
    assert sleeps.calls == [1.0, 1.0, 0.5]


async def test_no_display_connected_does_nothing():
    bus = EventBus()
    sleeps = Sleeps()
    waker = make_waker(bus, sleeps=sleeps)
    appeared = AppearsAfter(0)
    assert await waker.wake("Kitchen iPad", appeared) is False
    assert sleeps.calls == []
    assert appeared.checks == 0


async def test_zero_wait_turns_waking_off():
    bus = EventBus()
    sleeps = Sleeps()
    waker = make_waker(bus, wait_s=0, sleeps=sleeps)
    with bus.subscribe() as sub:
        assert await waker.wake(None, AppearsAfter(0)) is False
        assert sub.get_nowait() is None
    assert sleeps.calls == []


def test_default_wait_covers_the_kitchen_reconnect(settings):
    # The Display came back about 6 s after opening Spotify; 4 s was too short.
    assert settings.spotify.wake_wait_s >= 10
    assert settings.spotify.wake_poll_s == 1.0


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


async def test_service_polls_until_the_ipad_is_listed(conn, settings):
    fake = FakeSpotify()
    # Missing at first, then for five polls; listed on the sixth (about 6 s after the wake).
    fake.queue(
        "GET",
        "/v1/me/player/devices",
        *[json_response(200, "devices_no_ipad") for _ in range(6)],
    )
    bus = EventBus()
    clock = Clock()
    sleeps = Sleeps(clock)
    waker = make_waker(bus, clock=clock, sleeps=sleeps)
    service = _service(conn, settings, fake, waker)
    with bus.subscribe() as sub:
        device = await service.target_device()
        assert isinstance(sub.get_nowait(), SpotifyWakeRequestedEvent)
    assert device.id == IPAD_ID
    assert len(fake.calls("GET", "/v1/me/player/devices")) == 7
    assert sleeps.calls == [1.0] * 6


async def test_guided_access_display_ignores_it_and_coco_says_open_spotify(conn, settings):
    """Under Guided Access the Display does nothing, so the iPad never appears."""
    fake = FakeSpotify()
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    bus = EventBus()
    waker = make_waker(bus)
    service = _service(conn, settings, fake, waker)
    with bus.subscribe():
        with pytest.raises(SpotifyNoDevice) as exc:
            await service.target_device()
    assert exc.value.say == "Open Spotify on the iPad once, then try again."
    assert len(fake.calls("GET", "/v1/me/player/devices")) == 1 + 12
