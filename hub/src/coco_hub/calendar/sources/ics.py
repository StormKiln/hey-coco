"""iCalendar (ICS) feeds over HTTPS: any ``.ics`` URL, e.g. Google's "Secret address in iCal
format" (docs/calendar.md, spike docs/spikes/calendar.md).

The only module that imports ``icalendar``, ``recurring_ical_events`` and ``x_wr_timezone``.

- The URL is a bearer secret: it is a ``SecretStr``, only ever handed to httpx, and every
  error raised from here is built without it (``from None``: httpx's own messages name it).
- Conditional requests (``If-None-Match`` / ``If-Modified-Since``) when the server sends
  validators; on ``304`` the last feed is re-expanded for the new window. Google sends none
  (spike #138), so every poll is a full download there.
- Parsing and recurrence expansion run in a worker thread (``asyncio.to_thread``): a
  900-event feed takes about 0.25 s.
"""

import asyncio
import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
import x_wr_timezone
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from coco_hub.calendar.model import (
    BUSY,
    AuthRequired,
    BadFeed,
    CalendarEvent,
    CalendarRef,
    FetchResult,
    Unreachable,
)

# Identifies the software only — never anything about the household.
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
MAX_BYTES = 20 * 1024 * 1024
"""Refuse bigger feeds (a large shared calendar is well under 1 MB)."""
_SCHEMES = {"https": "https", "http": "http", "webcal": "https", "webcals": "https"}
_PRIVATE = {"PRIVATE", "CONFIDENTIAL"}
_REJECTED = {401, 403, 404, 410}
"""The address or its credentials are no longer accepted (Google answers 404 once a secret
address is reset)."""
UNTITLED = "Untitled event"


class IcsOptions(BaseModel):
    """``[[calendars]]`` keys for ``source = "ics"``."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    url: SecretStr
    """``https://``, ``http://`` or ``webcal://`` (fetched over https)."""

    @field_validator("url")
    @classmethod
    def _scheme(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value().strip()
        scheme, sep, rest = raw.partition("://")
        if not sep or scheme.lower() not in _SCHEMES or not rest:
            raise ValueError("url must start with https://, http:// or webcal://")
        return SecretStr(f"{_SCHEMES[scheme.lower()]}://{rest}")


class IcsSource:
    Options = IcsOptions

    def __init__(
        self,
        options: IcsOptions,
        *,
        tz: ZoneInfo,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = options.url
        self._tz = tz
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._raw: bytes | None = None
        self._validators: dict[str, str] = {}

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*;q=0.5"},
            )
        return self._http

    async def fetch(self, start: datetime, end: datetime) -> FetchResult:
        raw = await self._download()
        try:
            return await asyncio.to_thread(parse, raw, start, end, self._tz)
        except BadFeed:
            raise
        except Exception:  # the parser's message could quote the feed; keep the type only
            raise BadFeed("could not parse the feed") from None

    async def _download(self) -> bytes:
        headers = self._validators if self._raw is not None else {}
        try:
            async with self._client().stream(
                "GET", self._url.get_secret_value(), headers=headers
            ) as response:
                status = response.status_code
                if status == 304 and self._raw is not None:
                    return self._raw
                if status in _REJECTED:
                    raise AuthRequired(f"HTTP {status}")
                if status >= 400 or status == 304:
                    raise Unreachable(f"HTTP {status}")
                if int(response.headers.get("Content-Length") or 0) > MAX_BYTES:
                    raise BadFeed("feed too large")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise BadFeed("feed too large")
                validators = {}
                if etag := response.headers.get("ETag"):
                    validators["If-None-Match"] = etag
                if modified := response.headers.get("Last-Modified"):
                    validators["If-Modified-Since"] = modified
        except httpx.HTTPError as exc:  # the message names the URL: keep the type only
            raise Unreachable(type(exc).__name__) from None
        raw = bytes(body)
        if not raw.lstrip(b"\xef\xbb\xbf \t\r\n").upper().startswith(b"BEGIN:VCALENDAR"):
            raise BadFeed("not an iCalendar feed")
        self._raw, self._validators = raw, validators
        return raw

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


# --- parsing (runs in a worker thread) ---------------------------------------------------


def parse(raw: bytes, start: datetime, end: datetime, tz: ZoneInfo) -> FetchResult:
    calendar = x_wr_timezone.to_standard(icalendar.Calendar.from_ical(raw))
    name = str(calendar.get("X-WR-CALNAME", "")).strip() or None
    events = []
    for component in recurring_ical_events.of(calendar).between(start, end):
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if (event := _event(component, tz)) is not None:
            events.append(event)
    events.sort(key=lambda e: (e.start, e.end, e.title))
    return FetchResult(name=name, events=events)


def _event(component: Any, tz: ZoneInfo) -> CalendarEvent | None:
    dtstart = component.get("DTSTART")
    if dtstart is None:
        return None
    start_value = dtstart.dt
    all_day = not isinstance(start_value, datetime)
    start = _local(start_value, tz)
    end = _end(component, start_value, tz, all_day)
    private = str(component.get("CLASS", "")).upper() in _PRIVATE
    title = BUSY if private else (str(component.get("SUMMARY", "")).strip() or UNTITLED)
    rid = component.get("RECURRENCE-ID")
    original = rid.dt if rid is not None else start_value
    key = f"{component.get('UID', '')}|{_iso(original)}"
    return CalendarEvent(
        id=hashlib.sha256(key.encode()).hexdigest()[:16],
        title=title,
        start=start,
        end=max(end, start),
        all_day=all_day,
        calendar=CalendarRef(name=""),
        busy_only=private,
    )


def _end(component: Any, start_value: date | datetime, tz: ZoneInfo, all_day: bool) -> datetime:
    if (dtend := component.get("DTEND")) is not None:
        return _local(dtend.dt, tz)
    if (duration := component.get("DURATION")) is not None:
        return _local(start_value, tz) + duration.dt
    return _local(start_value, tz) + (timedelta(days=1) if all_day else timedelta(0))


def _local(value: date | datetime, tz: ZoneInfo) -> datetime:
    """Hub-local aware datetime: dates at local midnight, floating times in the Hub's zone."""
    if not isinstance(value, datetime):
        return datetime.combine(value, time(0), tzinfo=tz)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _iso(value: date | datetime) -> str:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(ZoneInfo("UTC")).isoformat()
    return value.isoformat()
