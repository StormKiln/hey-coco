"""Open-Meteo forecast API client (https://open-meteo.com/, no API key).

We ask for times as Unix timestamps so every instant is unambiguous; Open-Meteo still lays out
the hourly and daily series in the Hub's timezone (hourly from local midnight today, one daily
entry per local date).
"""

from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from coco_hub.protocol import TemperatureUnit, WeatherNow
from coco_hub.weather import codes
from coco_hub.weather.model import Day, Forecast, Hour

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Identifies the software only — never anything about the household.
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"
FORECAST_DAYS = 7
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

Units = Literal["metric", "imperial"]

_CURRENT = ("temperature_2m", "apparent_temperature", "weather_code", "is_day")
_HOURLY = (
    "temperature_2m",
    "apparent_temperature",
    "weather_code",
    "precipitation_probability",
    "is_day",
)
_DAILY = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
)


class WeatherFetchError(Exception):
    """The forecast couldn't be fetched or understood."""


class OpenMeteoClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        url: str = FORECAST_URL,
    ) -> None:
        self._url = url
        self._transport = transport
        self._timeout = timeout
        # Created on first use and dropped on close, so the client can be reused across
        # app restarts (and event loops) without holding a stale connection pool.
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    async def forecast(self, *, lat: float, lon: float, timezone: str, units: Units) -> Forecast:
        params: dict[str, str | int | float] = {
            "latitude": lat,
            "longitude": lon,
            "current": ",".join(_CURRENT),
            "hourly": ",".join(_HOURLY),
            "daily": ",".join(_DAILY),
            "timezone": timezone,
            "timeformat": "unixtime",
            "forecast_days": FORECAST_DAYS,
        }
        if units == "imperial":
            params["temperature_unit"] = "fahrenheit"
        try:
            response = await self._client().get(self._url, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherFetchError(f"Open-Meteo request failed: {exc}") from exc
        return parse_forecast(data, units=units, timezone=timezone)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"expected a number, got {value!r}")
    return float(value)


def _percent(value: Any) -> int:
    return 0 if value is None else max(0, min(100, round(_num(value))))


def _instant(value: Any) -> datetime:
    return datetime.fromtimestamp(_num(value), UTC)


def _column(block: dict[str, Any], key: str, length: int) -> list[Any]:
    values = block[key]
    if not isinstance(values, list) or len(values) != length:
        raise ValueError(f"{key} has the wrong length")
    return values


def parse_forecast(data: Any, *, units: Units, timezone: str) -> Forecast:
    """Turn an Open-Meteo JSON response (``timeformat=unixtime``) into a ``Forecast``.

    Hours missing a temperature or code are dropped; a missing precipitation probability
    reads as 0. Anything structurally wrong raises ``WeatherFetchError``.
    """
    unit: TemperatureUnit = "F" if units == "imperial" else "C"
    tz = ZoneInfo(timezone)
    try:
        cur = data["current"]
        code = int(_num(cur["weather_code"]))
        current = WeatherNow(
            time=_instant(cur["time"]),
            temp=_num(cur["temperature_2m"]),
            feels_like=_num(cur["apparent_temperature"]),
            unit=unit,
            code=code,
            description=codes.describe(code),
            is_day=bool(cur["is_day"]),
        )

        hourly = data["hourly"]
        times = hourly["time"]
        n = len(times)
        columns = [_column(hourly, key, n) for key in _HOURLY]
        hours = []
        for t, temp, feels, wcode, precip, is_day in zip(times, *columns, strict=True):
            if temp is None or wcode is None:
                continue
            hours.append(
                Hour(
                    time=_instant(t),
                    temp=_num(temp),
                    feels_like=_num(temp if feels is None else feels),
                    code=int(_num(wcode)),
                    precip_probability=_percent(precip),
                    is_day=bool(is_day),
                )
            )

        daily = data["daily"]
        times = daily["time"]
        n = len(times)
        columns = [_column(daily, key, n) for key in _DAILY]
        days = []
        for t, wcode, high, low, precip in zip(times, *columns, strict=True):
            if high is None or low is None or wcode is None:
                continue
            days.append(
                Day(
                    date=_instant(t).astimezone(tz).date(),
                    high=_num(high),
                    low=_num(low),
                    code=int(_num(wcode)),
                    precip_probability=_percent(precip),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherFetchError(f"unexpected Open-Meteo response: {exc!r}") from exc
    return Forecast(current=current, hours=tuple(hours), days=tuple(days), unit=unit)
