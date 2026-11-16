"""Weather for home: Open-Meteo behind a small cache.

- A forecast is reused for ``ttl`` (10 minutes); the app also refreshes it in the background
  (``run``) so the idle screen and questions rarely wait on the network.
- When a fetch fails, the last good forecast is returned marked ``stale``, and we don't try
  again for ``retry_after`` so an offline Hub answers instantly.
- ``None`` means there is nothing to show: the location isn't set, or no fetch has ever worked.
- Callers never wait on the network for long: ``get`` waits at most ``max_wait`` for a fetch,
  then answers from the cache while the fetch carries on in the background. A black-holed
  upstream (connection opens, nothing answers) would otherwise hold every caller for the
  client's full timeout. Only one fetch runs at a time; concurrent callers join it.
- Somewhere else (#93): ``resolve`` geocodes a place name (``places.Geocoder``) and
  ``get_at`` fetches that place's forecast in its own timezone, cached for ``ttl`` per place.
  These don't need a home location.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx

from coco_hub.settings import Settings
from coco_hub.weather.client import OpenMeteoClient, WeatherFetchError
from coco_hub.weather.model import Forecast
from coco_hub.weather.places import Geocoder, Pick, Place

log = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=10)
REFRESH_EVERY = timedelta(minutes=9)
RETRY_AFTER = timedelta(minutes=1)
GET_MAX_WAIT_S = 3.0
"""Longest ``get`` waits for a fetch by default (a spoken weather question)."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class WeatherSnapshot:
    forecast: Forecast
    fetched_at: datetime
    stale: bool = False


class WeatherService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: OpenMeteoClient | None = None,
        geocoder: Geocoder | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        ttl: timedelta = CACHE_TTL,
        retry_after: timedelta = RETRY_AFTER,
    ) -> None:
        self._settings = settings
        self._client = client or OpenMeteoClient(
            transport=transport, url=settings.weather.forecast_url
        )
        self._geocoder = geocoder or Geocoder(
            transport=transport, url=settings.weather.geocoding_url
        )
        self._places: dict[tuple[float, float], WeatherSnapshot] = {}
        self._clock = clock
        self._ttl = ttl
        self._retry_after = retry_after
        self._snapshot: WeatherSnapshot | None = None
        self._failed_at: datetime | None = None
        self._inflight: asyncio.Task[WeatherSnapshot | None] | None = None

    @property
    def configured(self) -> bool:
        loc = self._settings.location
        return loc.lat is not None and loc.lon is not None

    @property
    def snapshot(self) -> WeatherSnapshot | None:
        """The cached forecast, without fetching (diagnostics)."""
        return self._snapshot

    @property
    def last_failed_at(self) -> datetime | None:
        """When the last fetch failed, or None if the last fetch worked (diagnostics)."""
        return self._failed_at

    def _fresh(self, now: datetime) -> bool:
        return self._snapshot is not None and now - self._snapshot.fetched_at < self._ttl

    def _backing_off(self, now: datetime) -> bool:
        return self._failed_at is not None and now - self._failed_at < self._retry_after

    async def get(self, max_wait: float = GET_MAX_WAIT_S) -> WeatherSnapshot | None:
        """The cached forecast if fresh, else a new one (or the last good one, stale).

        Waits at most ``max_wait`` seconds for a fetch; past that, returns what the cache
        has (possibly None) and lets the fetch finish in the background.
        """
        if not self.configured:
            return None
        now = self._clock()
        if self._fresh(now) or self._backing_off(now):
            return self._snapshot
        try:
            return await asyncio.wait_for(asyncio.shield(self._start_fetch()), max_wait)
        except TimeoutError:
            return self._snapshot

    async def refresh(self) -> WeatherSnapshot | None:
        """Fetch now, ignoring the cache (or join the fetch already running)."""
        if not self.configured:
            return None
        return await asyncio.shield(self._start_fetch())

    def _start_fetch(self) -> asyncio.Task[WeatherSnapshot | None]:
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._fetch(), name="weather-fetch")
            self._inflight.add_done_callback(_log_crash)
        return self._inflight

    async def _fetch(self) -> WeatherSnapshot | None:
        loc = self._settings.location
        assert loc.lat is not None and loc.lon is not None
        try:
            forecast = await self._client.forecast(
                lat=loc.lat,
                lon=loc.lon,
                timezone=self._settings.timezone,
                units=self._settings.units,
            )
        except WeatherFetchError as exc:
            log.warning("weather refresh failed: %s", exc)
            self._failed_at = self._clock()
            if self._snapshot is not None and not self._snapshot.stale:
                self._snapshot = replace(self._snapshot, stale=True)
            return self._snapshot
        self._failed_at = None
        self._snapshot = WeatherSnapshot(forecast=forecast, fetched_at=self._clock())
        return self._snapshot

    async def resolve(self, query: str) -> Pick | None:
        """The place ``query`` names, or None. Raises ``WeatherFetchError`` when offline."""
        return await self._geocoder.resolve(query)

    async def get_at(self, place: Place) -> WeatherSnapshot:
        """``place``'s forecast, in its timezone. Raises ``WeatherFetchError``."""
        key = (place.lat, place.lon)
        now = self._clock()
        cached = self._places.get(key)
        if cached is not None and now - cached.fetched_at < self._ttl:
            return cached
        forecast = await self._client.forecast(
            lat=place.lat, lon=place.lon, timezone=place.timezone, units=self._settings.units
        )
        snapshot = WeatherSnapshot(forecast=forecast, fetched_at=now)
        self._places = {k: v for k, v in self._places.items() if now - v.fetched_at < self._ttl}
        self._places[key] = snapshot
        return snapshot

    async def run(self, every: timedelta = REFRESH_EVERY) -> None:
        """Keep the cache warm until cancelled."""
        while True:
            try:
                await self.refresh()
            except Exception:  # never let the background loop die
                log.exception("weather refresh crashed")
            await asyncio.sleep(every.total_seconds())

    async def aclose(self) -> None:
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
            with suppress(asyncio.CancelledError):
                await self._inflight
        self._inflight = None
        await self._client.aclose()
        await self._geocoder.aclose()


def _log_crash(task: asyncio.Task[WeatherSnapshot | None]) -> None:
    """Retrieve (and log) an unexpected error from a fetch nobody waited for."""
    if not task.cancelled() and (exc := task.exception()) is not None:
        log.error("weather fetch crashed", exc_info=exc)
