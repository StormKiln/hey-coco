import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.protocol import IdleInfo
from coco_hub.weather.service import WeatherService

from .conftest import FIXED
from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


def idle(client) -> IdleInfo:
    response = client.get("/v1/idle", headers=AUTH)
    assert response.status_code == 200
    info = IdleInfo.model_validate(response.json())
    # What we send round-trips through the protocol model unchanged.
    assert info.model_dump(mode="json") == response.json()
    return info


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_idle_requires_token(settings, headers):
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/idle", headers=headers)
    assert response.status_code == 401


def test_idle_without_location_has_no_weather(settings):
    night = settings.night.model_copy(update={"start": datetime(1, 1, 1, 22, 15).time()})
    s = settings.model_copy(update={"timezone": "America/Toronto", "night": night})
    with TestClient(create_app(s, clock=lambda: FIXED)) as client:
        info = idle(client)
    assert info.server_time == FIXED
    assert info.timezone == "America/Toronto"
    assert info.weather is None
    assert info.night.model_dump() == {"start": "22:15", "end": "06:30", "dark_after_idle_s": 300}


def test_idle_with_weather(home, clock, fake_open_meteo):
    service = WeatherService(home, transport=fake_open_meteo.transport, clock=clock)
    with TestClient(create_app(home, clock=clock, weather=service)) as client:
        info = idle(client)
    assert info.server_time == FIXED
    weather = info.weather
    assert weather is not None
    assert not weather.stale
    assert weather.current.temp == 4.1
    assert weather.current.time == datetime(2026, 11, 14, 15, 15, tzinfo=UTC)
    # The next 12 whole hours, starting with the one after now (10:20 local).
    assert len(weather.hourly) == 12
    assert weather.hourly[0].time == datetime(2026, 11, 14, 16, tzinfo=UTC)
    assert weather.hourly[0].temp == 3.9
    assert weather.hourly[-1].time == datetime(2026, 11, 15, 3, tzinfo=UTC)


def test_idle_marks_weather_stale_when_refresh_fails(home, clock, fake_open_meteo):
    service = WeatherService(home, transport=fake_open_meteo.transport, clock=clock)
    with TestClient(create_app(home, clock=clock, weather=service)) as client:
        assert not idle(client).weather.stale
        fake_open_meteo.fail = True
        clock.advance(minutes=15)
        info = idle(client)
    assert info.weather.stale
    assert info.weather.current.temp == 4.1
    # Hourly still moves with the clock (now 10:35 local -> starts at 11:00).
    assert info.weather.hourly[0].time == datetime(2026, 11, 14, 16, tzinfo=UTC)


def test_idle_weather_null_when_never_fetched(home, clock, fake_open_meteo):
    fake_open_meteo.fail = True
    service = WeatherService(home, transport=fake_open_meteo.transport, clock=clock)
    with TestClient(create_app(home, clock=clock, weather=service)) as client:
        assert idle(client).weather is None


def test_lifespan_refreshes_weather_in_background(home, clock, fake_open_meteo):
    service = WeatherService(home, transport=fake_open_meteo.transport, clock=clock)
    with TestClient(create_app(home, clock=clock, weather=service)):
        deadline = time.monotonic() + 2
        while not fake_open_meteo.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(fake_open_meteo.requests) == 1


def test_app_registers_weather_service(settings):
    app = create_app(settings)
    assert isinstance(app.state.services["weather"], WeatherService)


def test_idle_answers_fast_when_the_weather_upstream_hangs(home, clock, fake_open_meteo):
    fake_open_meteo.hang = True  # internet "down" as a black hole: requests never return
    service = WeatherService(home, transport=fake_open_meteo.transport, clock=clock)
    with TestClient(create_app(home, clock=clock, weather=service)) as client:
        started = time.monotonic()
        info = idle(client)
        assert time.monotonic() - started < 1.0
    assert info.weather is None
