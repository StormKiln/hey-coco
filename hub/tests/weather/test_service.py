import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def test_unconfigured_location_returns_none_without_fetching(settings, make_service):
    service = make_service(settings)  # no lat/lon
    assert not service.configured
    assert await service.get() is None
    assert await service.refresh() is None
    await service.aclose()


async def test_first_get_fetches(make_service, fake_open_meteo, clock):
    service = make_service()
    snapshot = await service.get()
    assert snapshot is not None
    assert not snapshot.stale
    assert snapshot.fetched_at == clock.now
    assert snapshot.forecast.current.temp == 4.1
    assert len(fake_open_meteo.requests) == 1
    await service.aclose()


async def test_cache_is_reused_until_it_expires(make_service, fake_open_meteo, clock):
    service = make_service()
    first = await service.get()
    clock.advance(minutes=9, seconds=59)
    assert await service.get() is first
    assert len(fake_open_meteo.requests) == 1
    clock.advance(seconds=1)
    second = await service.get()
    assert second is not first
    assert second.fetched_at == clock.now
    assert len(fake_open_meteo.requests) == 2
    await service.aclose()


async def test_failure_returns_last_good_value_marked_stale(make_service, fake_open_meteo, clock):
    service = make_service()
    good = await service.get()
    fake_open_meteo.fail = True
    clock.advance(minutes=11)
    stale = await service.get()
    assert stale.stale
    assert stale.forecast is good.forecast
    assert stale.fetched_at == good.fetched_at
    assert len(fake_open_meteo.requests) == 2
    await service.aclose()


async def test_failure_backs_off_before_retrying(make_service, fake_open_meteo, clock):
    service = make_service()
    await service.get()
    fake_open_meteo.fail = 503
    clock.advance(minutes=11)
    await service.get()
    clock.advance(seconds=30)
    assert (await service.get()).stale
    assert len(fake_open_meteo.requests) == 2  # no retry inside the back-off
    fake_open_meteo.fail = False
    clock.advance(seconds=31)
    fresh = await service.get()
    assert not fresh.stale
    assert len(fake_open_meteo.requests) == 3
    await service.aclose()


async def test_never_fetched_and_failing_returns_none(make_service, fake_open_meteo):
    fake_open_meteo.fail = True
    service = make_service()
    assert await service.get() is None
    await service.aclose()


async def test_refresh_ignores_cache(make_service, fake_open_meteo):
    service = make_service()
    await service.get()
    await service.refresh()
    assert len(fake_open_meteo.requests) == 2
    await service.aclose()


async def test_concurrent_gets_fetch_once(make_service, fake_open_meteo):
    service = make_service()
    results = await asyncio.gather(*(service.get() for _ in range(5)))
    assert len(fake_open_meteo.requests) == 1
    assert all(r is results[0] for r in results)
    await service.aclose()


async def test_imperial_settings_request_fahrenheit(home, make_service):
    from .conftest import FakeOpenMeteo

    fake = FakeOpenMeteo("ottawa_imperial")
    service = make_service(home.model_copy(update={"units": "imperial"}), fake)
    snapshot = await service.get()
    assert snapshot.forecast.unit == "F"
    assert fake.requests[0].url.params["temperature_unit"] == "fahrenheit"
    await service.aclose()


async def test_forecast_url_comes_from_settings(home, clock, fake_open_meteo):
    from coco_hub.weather.service import WeatherService

    custom = home.model_copy(
        update={"weather": home.weather.model_copy(update={"forecast_url": "http://wx.test/f"})}
    )
    service = WeatherService(custom, transport=fake_open_meteo.transport, clock=clock)
    await service.get()
    assert str(fake_open_meteo.requests[0].url).startswith("http://wx.test/f?")
    await service.aclose()


async def test_get_does_not_wait_long_for_a_hanging_fetch(make_service, fake_open_meteo):
    fake_open_meteo.hang = True
    service = make_service()
    started = asyncio.get_running_loop().time()
    assert await service.get(max_wait=0.05) is None
    assert asyncio.get_running_loop().time() - started < 0.5
    # The fetch carries on in the background and fills the cache once it answers.
    fake_open_meteo.release()
    snapshot = await service.refresh()
    assert snapshot is not None and not snapshot.stale
    assert len(fake_open_meteo.requests) == 1  # refresh joined the in-flight fetch
    await service.aclose()


async def test_get_returns_the_old_forecast_while_a_slow_refresh_runs(
    make_service, fake_open_meteo, clock
):
    service = make_service()
    good = await service.get()
    clock.advance(minutes=11)
    fake_open_meteo.hang = True
    assert await service.get(max_wait=0.05) is good
    await service.aclose()


async def test_aclose_cancels_a_hanging_fetch(make_service, fake_open_meteo):
    fake_open_meteo.hang = True
    service = make_service()
    await service.get(max_wait=0.01)
    await asyncio.wait_for(service.aclose(), timeout=1)
