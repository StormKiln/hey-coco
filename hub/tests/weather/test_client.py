from datetime import UTC, date, datetime

import httpx
import pytest

from coco_hub.weather.client import (
    USER_AGENT,
    OpenMeteoClient,
    WeatherFetchError,
    parse_forecast,
)

from .conftest import FakeOpenMeteo, load

pytestmark = pytest.mark.anyio


def test_parse_recorded_metric_response():
    forecast = parse_forecast(load("ottawa_metric"), units="metric", timezone="America/Toronto")
    now = forecast.current
    assert now.time == datetime(2026, 11, 14, 15, 15, tzinfo=UTC)
    assert (now.temp, now.feels_like, now.unit) == (4.1, -0.1, "C")
    assert (now.code, now.description, now.is_day) == (3, "Overcast", True)
    assert forecast.unit == "C"
    assert len(forecast.hours) == 168
    first = forecast.hours[0]
    assert first.time == datetime(2026, 11, 14, 5, 0, tzinfo=UTC)  # local midnight
    assert (first.temp, first.feels_like, first.code) == (1.6, -1.7, 0)
    assert (first.precip_probability, first.is_day) == (1, False)
    assert [d.date for d in forecast.days][:2] == [date(2026, 11, 14), date(2026, 11, 15)]
    assert (forecast.days[0].high, forecast.days[0].low) == (4.4, 0.4)
    assert (forecast.days[0].code, forecast.days[0].precip_probability) == (51, 21)
    assert len(forecast.days) == 7


def test_parse_recorded_imperial_response():
    forecast = parse_forecast(load("ottawa_imperial"), units="imperial", timezone="America/Toronto")
    assert forecast.unit == "F"
    assert forecast.current.unit == "F"
    assert forecast.current.temp == 39.4
    assert forecast.days[1].high == 40.3


def test_parse_tolerates_null_values():
    data = load("ottawa_metric")
    data["hourly"]["precipitation_probability"][3] = None
    data["hourly"]["temperature_2m"][4] = None
    data["daily"]["precipitation_probability_max"][6] = None
    forecast = parse_forecast(data, units="metric", timezone="America/Toronto")
    assert forecast.hours[3].precip_probability == 0
    assert len(forecast.hours) == 167  # the hour without a temperature is dropped
    assert forecast.days[6].precip_probability == 0


@pytest.mark.parametrize(
    "mangle",
    [
        lambda d: d.pop("current"),
        lambda d: d["hourly"].pop("weather_code"),
        lambda d: d["daily"].update(temperature_2m_max=[1.0]),
        lambda d: d["current"].update(temperature_2m="warm"),
        lambda d: d["current"].update(temperature_2m=None),
    ],
)
def test_parse_rejects_malformed_responses(mangle):
    data = load("ottawa_metric")
    mangle(data)
    with pytest.raises(WeatherFetchError):
        parse_forecast(data, units="metric", timezone="America/Toronto")


async def test_request_parameters_metric():
    fake = FakeOpenMeteo()
    client = OpenMeteoClient(transport=fake.transport)
    await client.forecast(lat=45.42, lon=-75.69, timezone="America/Toronto", units="metric")
    await client.aclose()
    (request,) = fake.requests
    params = request.url.params
    assert request.url.host == "api.open-meteo.com"
    assert request.url.path == "/v1/forecast"
    assert (params["latitude"], params["longitude"]) == ("45.42", "-75.69")
    assert params["timezone"] == "America/Toronto"
    assert params["timeformat"] == "unixtime"
    assert set(params["current"].split(",")) == {
        "temperature_2m",
        "apparent_temperature",
        "weather_code",
        "is_day",
    }
    assert set(params["hourly"].split(",")) == {
        "temperature_2m",
        "apparent_temperature",
        "weather_code",
        "precipitation_probability",
        "is_day",
    }
    assert set(params["daily"].split(",")) == {
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_probability_max",
    }
    assert "temperature_unit" not in params
    assert request.headers["user-agent"] == USER_AGENT
    assert USER_AGENT == "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"


async def test_request_parameters_imperial():
    fake = FakeOpenMeteo("ottawa_imperial")
    client = OpenMeteoClient(transport=fake.transport)
    forecast = await client.forecast(lat=1, lon=2, timezone="UTC", units="imperial")
    await client.aclose()
    assert fake.requests[0].url.params["temperature_unit"] == "fahrenheit"
    assert forecast.unit == "F"


@pytest.mark.parametrize("fail", [True, 500, 400])
async def test_http_failures_raise(fail):
    fake = FakeOpenMeteo()
    fake.fail = fail
    client = OpenMeteoClient(transport=fake.transport)
    with pytest.raises(WeatherFetchError):
        await client.forecast(lat=1, lon=2, timezone="UTC", units="metric")
    await client.aclose()


async def test_non_json_body_raises():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops</html>"))
    client = OpenMeteoClient(transport=transport)
    with pytest.raises(WeatherFetchError):
        await client.forecast(lat=1, lon=2, timezone="UTC", units="metric")
    await client.aclose()
