"""Calendars for the idle screen and voice answers, behind a small cache (#139).

Modelled on ``weather.service.WeatherService``:

- Every configured ``[[calendars]]`` entry is read by its source (``sources``), polled every
  ``POLL_EVERY`` (15 min) with +/-10% jitter, for the window from local midnight yesterday to
  local midnight 15 days from today (-1 d .. +14 d).
- The last good events of each calendar are persisted to SQLite (``calendar_cache``) so a
  restarted Hub shows them before its first fetch.
- A failed fetch keeps that calendar's last good events and marks it stale, with the
  ``fetched_at`` of the data it still has. Data older than ``STALE_AFTER`` is stale too.
- Names, colours, ``busy_only`` and ``show_on_idle`` are applied when reading, so a config
  change takes effect on the next restart without refetching.
- Logs and ``status()`` name calendars by position ("calendar 2") and say only why a fetch
  failed: source options (URLs) are secrets.
- Nothing else waits on this: the idle screen and voice answers read ``view()``, which never
  touches the network.
"""

import asyncio
import hashlib
import json
import logging
import random
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter

from coco_hub.calendar.model import BUSY, CalendarError, CalendarEvent, CalendarRef
from coco_hub.calendar.sources import CalendarSource, source_class
from coco_hub.settings import CalendarConfig, Settings

log = logging.getLogger(__name__)

POLL_EVERY = 15 * 60.0
"""Seconds between polls (before jitter)."""
JITTER = 0.1
STALE_AFTER = timedelta(minutes=35)
"""Data this old is stale even if no fetch failed (two missed polls)."""
DAYS_BACK = 1
DAYS_AHEAD = 14
PALETTE = ("#1A73E8", "#33B679", "#E67C73", "#F6BF26", "#8E24AA", "#F4511E", "#039BE5")
"""Default colours by position, for calendars without ``color``."""

_EVENTS = TypeAdapter(list[CalendarEvent])

SourceFactory = Callable[[CalendarConfig, ZoneInfo], CalendarSource]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CalendarStatus:
    """One calendar's freshness, for ``/v1/diagnostics``: no names, no URLs."""

    index: int
    """1-based position in ``[[calendars]]``."""
    fetched_at: datetime | None
    stale: bool
    error: str | None
    """Why the last fetch failed (``unreachable``, ``auth_required``, ``bad_feed``,
    ``error``), or None if it worked."""


@dataclass(frozen=True)
class CalendarView:
    events: list[CalendarEvent]
    """Every cached event, sorted by start, names/colours/privacy applied."""
    stale: bool
    """Some calendar's data is missing, failed to refresh or is older than ``STALE_AFTER``."""
    fetched_at: datetime | None
    """The oldest calendar's last good fetch (None: some calendar never fetched)."""

    def between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Events overlapping ``[start, end)``."""
        return [e for e in self.events if e.end > start and e.start < end]


class _Calendar:
    def __init__(self, index: int, config: CalendarConfig, source: CalendarSource) -> None:
        self.index = index
        self.config = config
        self.source = source
        self.key = cache_key(config)
        self.feed_name: str | None = None
        self.events: list[CalendarEvent] = []
        self.fetched_at: datetime | None = None
        self.error: str | None = None

    @property
    def ref(self) -> CalendarRef:
        name = self.config.name or self.feed_name or f"Calendar {self.index}"
        color = self.config.color or PALETTE[(self.index - 1) % len(PALETTE)]
        return CalendarRef(name=name, color=color)

    def stale(self, now: datetime) -> bool:
        return (
            self.error is not None or self.fetched_at is None or now - self.fetched_at > STALE_AFTER
        )

    def shown(self) -> list[CalendarEvent]:
        ref = self.ref
        busy = self.config.busy_only
        return [
            e.model_copy(
                update={"calendar": ref, "busy_only": True, "title": BUSY}
                if busy
                else {"calendar": ref}
            )
            for e in self.events
        ]


def cache_key(config: CalendarConfig) -> str:
    """Identifies a calendar across restarts by its source and options (not its position),
    hashed: the options hold secrets."""
    payload = {"source": config.source, "options": _reveal(config.options)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :24
    ]


def _reveal(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {k: _reveal(getattr(value, k)) for k in type(value).model_fields}
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def default_source_factory(
    transport: httpx.AsyncBaseTransport | None = None,
) -> SourceFactory:
    def build(config: CalendarConfig, tz: ZoneInfo) -> CalendarSource:
        return source_class(config.source)(config.options, tz=tz, transport=transport)

    return build


class CalendarService:
    def __init__(
        self,
        settings: Settings,
        *,
        db: sqlite3.Connection | None = None,
        clock: Callable[[], datetime] = _utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
        source_factory: SourceFactory | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._tz = ZoneInfo(settings.timezone)
        self._db: sqlite3.Connection | None = None
        self._clock = clock
        self._rng = rng or random.Random()
        factory = source_factory or default_source_factory(transport)
        self._calendars = [
            _Calendar(i, config, factory(config, self._tz))
            for i, config in enumerate(settings.calendars, start=1)
        ]
        if db is not None:
            self.attach_db(db)

    @property
    def configured(self) -> bool:
        return bool(self._calendars)

    # --- reading ---------------------------------------------------------------------------

    def view(self, *, idle: bool = False) -> CalendarView | None:
        """What the cache holds (never fetches). None when no calendars are configured.
        ``idle``: leave out calendars with ``show_on_idle = false``."""
        if not self._calendars:
            return None
        now = self._clock()
        calendars = [c for c in self._calendars if not idle or c.config.show_on_idle]
        events = sorted(
            (e for c in calendars for e in c.shown()),
            key=lambda e: (e.start, not e.all_day, e.end, e.title),
        )
        fetched = [c.fetched_at for c in calendars]
        return CalendarView(
            events=events,
            stale=any(c.stale(now) for c in calendars),
            fetched_at=None if not fetched or None in fetched else min(fetched),  # type: ignore[type-var]
        )

    def status(self) -> list[CalendarStatus]:
        now = self._clock()
        return [
            CalendarStatus(
                index=c.index, fetched_at=c.fetched_at, stale=c.stale(now), error=c.error
            )
            for c in self._calendars
        ]

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    # --- fetching --------------------------------------------------------------------------

    def window(self) -> tuple[datetime, datetime]:
        today = self._clock().astimezone(self._tz).date()
        start = datetime.combine(today - timedelta(days=DAYS_BACK), time(0), tzinfo=self._tz)
        end = datetime.combine(today + timedelta(days=DAYS_AHEAD + 1), time(0), tzinfo=self._tz)
        return start, end

    async def refresh(self) -> CalendarView | None:
        """Fetch every calendar now (concurrently). Failures keep the last good data."""
        if not self._calendars:
            return None
        start, end = self.window()
        await asyncio.gather(*(self._fetch(c, start, end) for c in self._calendars))
        return self.view()

    async def _fetch(self, calendar: _Calendar, start: datetime, end: datetime) -> None:
        try:
            result = await calendar.source.fetch(start, end)
        except CalendarError as exc:
            calendar.error = exc.kind
            log.warning("calendar %d refresh failed: %s", calendar.index, exc.kind)
            return
        except Exception as exc:  # a source bug; its message could hold a secret
            calendar.error = "error"
            log.error("calendar %d refresh crashed: %s", calendar.index, type(exc).__name__)
            return
        calendar.feed_name = result.name
        calendar.events = list(result.events)
        calendar.fetched_at = self._clock()
        calendar.error = None
        self._save(calendar)

    def next_delay(self) -> float:
        """Seconds until the next poll: ``POLL_EVERY`` +/- ``JITTER``."""
        return POLL_EVERY * self._rng.uniform(1 - JITTER, 1 + JITTER)

    async def run(self) -> None:
        """Keep the cache warm until cancelled."""
        while True:
            try:
                await self.refresh()
            except Exception:  # never let the background loop die
                log.error("calendar refresh loop crashed", exc_info=False)
            await asyncio.sleep(self.next_delay())

    async def aclose(self) -> None:
        for calendar in self._calendars:
            with suppress(Exception):
                await calendar.source.aclose()

    # --- persistence -----------------------------------------------------------------------

    def attach_db(self, db: sqlite3.Connection) -> None:
        """Persist to ``db`` and load the last good events saved there (the app opens the
        database at startup, after building the service)."""
        self._db = db
        keys = [c.key for c in self._calendars]
        marks = ",".join("?" * len(keys))
        with self._db:  # forget calendars no longer configured
            self._db.execute(f"DELETE FROM calendar_cache WHERE cal_key NOT IN ({marks})", keys)
        rows = {
            row[0]: row[1:]
            for row in self._db.execute(
                "SELECT cal_key, feed_name, events, fetched_at FROM calendar_cache"
            )
        }
        for calendar in self._calendars:
            if (row := rows.get(calendar.key)) is None:
                continue
            feed_name, events, fetched_at = row
            try:
                calendar.events = _EVENTS.validate_json(events)
            except ValueError:
                log.warning("calendar %d: cached events unreadable; refetching", calendar.index)
                continue
            calendar.feed_name = feed_name
            calendar.fetched_at = datetime.fromisoformat(fetched_at)

    def _save(self, calendar: _Calendar) -> None:
        if self._db is None or calendar.fetched_at is None:
            return
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO calendar_cache (cal_key, feed_name, events, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    calendar.key,
                    calendar.feed_name,
                    _EVENTS.dump_json(calendar.events).decode(),
                    calendar.fetched_at.isoformat(),
                ),
            )
