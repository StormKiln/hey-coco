"""Weather test helpers: recorded Open-Meteo responses served by an httpx MockTransport.

Fixtures in ``tests/fixtures/open_meteo/``:
- ``ottawa_metric.json`` / ``ottawa_imperial.json``: real responses recorded 2026-11-14 10:17
  America/Toronto for lat 45.42 lon -75.69 (Ottawa, a generic public location).
- ``rain_afternoon.json``: the metric recording with rain 12:00-17:00 local on 2026-11-14.
- ``subzero.json``: the metric recording 10 degrees colder, with snow 12:00-16:00 on 11-15.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from coco_hub.weather.service import WeatherService

OPEN_METEO_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "open_meteo"
# 10:20 on Saturday 2026-11-14 in Toronto, five minutes after the recordings' "current".
FIXED = datetime(2026, 11, 14, 15, 20, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((OPEN_METEO_DIR / f"{name}.json").read_text())


class FakeOpenMeteo:
    """Serves a recorded response; flip ``fail`` to simulate the network being down."""

    def __init__(self, name: str = "ottawa_metric") -> None:
        self.name = name
        self.fail: bool | int = False
        """False, True (connection error) or an HTTP status code to return."""
        self.hang = False
        """True: requests wait until ``release()`` (a black-holed upstream)."""
        self._released = asyncio.Event()
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def release(self) -> None:
        self._released.set()

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.hang:
            await self._released.wait()
        if self.fail is True:
            raise httpx.ConnectError("offline", request=request)
        if self.fail:
            return httpx.Response(int(self.fail), json={"error": True, "reason": "nope"})
        return httpx.Response(200, json=load(self.name))


class Clock:
    def __init__(self, now: datetime = FIXED) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def fake_open_meteo() -> FakeOpenMeteo:
    return FakeOpenMeteo()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def home(settings):
    """Settings with a home location, in Toronto, metric."""
    return settings.model_copy(
        update={
            "timezone": "America/Toronto",
            "location": settings.location.model_copy(update={"lat": 45.42, "lon": -75.69}),
        }
    )


@pytest.fixture
def make_service(home, clock, fake_open_meteo) -> Callable[..., WeatherService]:
    def make(settings=None, fake: FakeOpenMeteo | None = None) -> WeatherService:
        return WeatherService(
            settings or home,
            transport=(fake or fake_open_meteo).transport,
            clock=clock,
        )

    return make
