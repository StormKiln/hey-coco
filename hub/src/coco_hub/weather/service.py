"""Weather for home: Open-Meteo behind a small cache.

- A forecast is reused for ``ttl`` (10 minutes); the app also refreshes it in the background
  (``run``) so the idle screen and questions rarely wait on the network.
- When a fetch fails, the last good forecast is returned marked ``stale``, and we don't try
  again for ``retry_after`` so an offline Hub answers instantly.
- ``None`` means there is nothing to show: the location isn't set, or no fetch has ever worked.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx

from coco_hub.settings import Settings
from coco_hub.weather.client import OpenMeteoClient, WeatherFetchError
from coco_hub.weather.model import Forecast

log = logging.getLogger(__name__)

CACHE_TTL = timedelta(minutes=10)
REFRESH_EVERY = timedelta(minutes=9)
RETRY_AFTER = timedelta(minutes=1)


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
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        ttl: timedelta = CACHE_TTL,
        retry_after: timedelta = RETRY_AFTER,
    ) -> None:
        self._settings = settings
        self._client = client or OpenMeteoClient(transport=transport)
        self._clock = clock
        self._ttl = ttl
        self._retry_after = retry_after
        self._snapshot: WeatherSnapshot | None = None
        self._failed_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        loc = self._settings.location
        return loc.lat is not None and loc.lon is not None

    def _fresh(self, now: datetime) -> bool:
        return self._snapshot is not None and now - self._snapshot.fetched_at < self._ttl

    def _backing_off(self, now: datetime) -> bool:
        return self._failed_at is not None and now - self._failed_at < self._retry_after

    async def get(self) -> WeatherSnapshot | None:
        """The cached forecast if fresh, else a new one (or the last good one, stale)."""
        if not self.configured:
            return None
        async with self._lock:
            now = self._clock()
            if self._fresh(now) or self._backing_off(now):
                return self._snapshot
            return await self._fetch()

    async def refresh(self) -> WeatherSnapshot | None:
        """Fetch now, ignoring the cache."""
        if not self.configured:
            return None
        async with self._lock:
            return await self._fetch()

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

    async def run(self, every: timedelta = REFRESH_EVERY) -> None:
        """Keep the cache warm until cancelled."""
        while True:
            try:
                await self.refresh()
            except Exception:  # never let the background loop die
                log.exception("weather refresh crashed")
            await asyncio.sleep(every.total_seconds())

    async def aclose(self) -> None:
        await self._client.aclose()
