"""YouTube Data API v3 search (https://developers.google.com/youtube/v3/docs/search/list).

``YoutubeClient`` does one ``search.list`` call; ``YoutubeService`` wraps it with what the
Hub needs:

- a missing API key (``keys.youtube``) is spoken as "YouTube isn't set up yet.";
- a query → results cache in SQLite for 24 hours (cache hits cost nothing);
- a daily quota guard: each search costs 100 units of the free 10,000/day. Usage is persisted
  per *quota day*, which YouTube resets at midnight Pacific time, and searches are refused
  past ``youtube.daily_quota`` (default 9,000) with a spoken message;
- the last results, so "next video" can play the following one.

Searches use ``safeSearch=moderate`` as a sensible default. That is *not* the lockdown:
content decisions belong to ``coco_hub.media.policy``.
"""

import html
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from coco_hub.settings import Settings

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
# Identifies the software only — never anything about the household.
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
SEARCH_COST = 100
MAX_RESULTS = 5
SAFE_SEARCH = "moderate"
CACHE_TTL = timedelta(hours=24)
QUOTA_TZ = ZoneInfo("America/Los_Angeles")

NOT_CONFIGURED_SAY = "YouTube isn't set up yet."
QUOTA_SAY = "I've used up today's YouTube searches. Try again tomorrow."
FETCH_FAILED_SAY = "I couldn't reach YouTube."
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    channel: str


class YoutubeUnavailable(Exception):
    """A search couldn't be done; ``say`` explains why to the household."""

    def __init__(self, say: str) -> None:
        super().__init__(say)
        self.say = say


class YoutubeFetchError(Exception):
    """The API couldn't be reached or answered with an error."""


class YoutubeQuotaError(YoutubeFetchError):
    """The API says the project's daily quota is used up."""


def quota_day(instant: datetime) -> str:
    """The YouTube quota day (``YYYY-MM-DD`` in Pacific time) an instant falls in."""
    return instant.astimezone(QUOTA_TZ).date().isoformat()


def parse_search(data: Any) -> list[Video]:
    """Videos from a ``search.list`` response, in order. Skips non-videos and premieres."""
    videos: list[Video] = []
    for item in data.get("items", []):
        ident = item.get("id") or {}
        snippet = item.get("snippet") or {}
        video_id = ident.get("videoId")
        if ident.get("kind") != "youtube#video" or not video_id:
            continue
        if snippet.get("liveBroadcastContent") == "upcoming":
            continue
        videos.append(
            Video(
                video_id=video_id,
                title=html.unescape(snippet.get("title", "")).strip(),
                channel=html.unescape(snippet.get("channelTitle", "")).strip(),
            )
        )
    return videos


_SEPARATOR = re.compile(r"\s+(?:\||-|–|—|:|·)\s+|\s*[(\[#]")
SHORT_TITLE = 40
MAX_SPOKEN = 60


def spoken_title(title: str) -> str:
    """A title short enough to say: cut at the first ``|`` / ``-`` / ``(`` etc. when long."""
    title = " ".join(title.split())
    if len(title) <= SHORT_TITLE:
        return title
    for m in _SEPARATOR.finditer(title):
        head = title[: m.start()].strip()
        if len(head) >= 8:
            return head if len(head) <= MAX_SPOKEN else _truncate(head)
    return _truncate(title)


def _truncate(text: str) -> str:
    if len(text) <= MAX_SPOKEN:
        return text
    cut = text[: MAX_SPOKEN + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")


def _error_reasons(response: httpx.Response) -> set[str]:
    try:
        errors = response.json()["error"]["errors"]
        return {e.get("reason", "") for e in errors}
    except (ValueError, KeyError, TypeError):
        return set()


class YoutubeClient:
    def __init__(
        self,
        api_key: str,
        *,
        region: str = "CA",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        url: str = SEARCH_URL,
    ) -> None:
        self._key = api_key
        self._region = region
        self._url = url
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    async def search(self, query: str) -> list[Video]:
        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "videoEmbeddable": "true",
            "safeSearch": SAFE_SEARCH,
            "maxResults": MAX_RESULTS,
            "regionCode": self._region,
            "q": query,
            "key": self._key,
        }
        try:
            response = await self._client().get(self._url, params=params)
        except httpx.HTTPError as exc:
            # Never include the URL: it carries the API key.
            raise YoutubeFetchError(f"YouTube request failed: {type(exc).__name__}") from None
        if response.status_code == 403 and _error_reasons(response) & _QUOTA_REASONS:
            raise YoutubeQuotaError("YouTube quota exceeded")
        if response.status_code != 200:
            raise YoutubeFetchError(f"YouTube answered HTTP {response.status_code}")
        try:
            return parse_search(response.json())
        except (ValueError, AttributeError, TypeError) as exc:
            raise YoutubeFetchError("YouTube sent an unreadable response") from exc

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


@dataclass
class LastResults:
    """The most recent search, for "next video"."""

    query: str
    videos: list[Video]
    index: int
    """Index of the video playing now."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class YoutubeService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        api_key: str | None,
        region: str = "CA",
        daily_budget: int = 9000,
        clock: Callable[[], datetime] = _utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._conn = conn
        self.region = region
        self.daily_budget = daily_budget
        self._clock = clock
        self._client = (
            YoutubeClient(api_key, region=region, transport=transport) if api_key else None
        )
        self.last: LastResults | None = None

    @classmethod
    def from_settings(
        cls,
        conn: sqlite3.Connection,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "YoutubeService":
        key = settings.keys.youtube.get_secret_value() if settings.keys.youtube else None
        return cls(
            conn,
            api_key=key or None,
            region=settings.youtube.region,
            daily_budget=settings.youtube.daily_quota,
            clock=clock,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self._client is not None

    # --- quota -----------------------------------------------------------------------

    def quota_used(self) -> int:
        row = self._conn.execute(
            "SELECT units FROM youtube_quota WHERE day = ?", (quota_day(self._clock()),)
        ).fetchone()
        return int(row[0]) if row else 0

    def _set_quota(self, units: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO youtube_quota (day, units) VALUES (?, ?) "
                "ON CONFLICT (day) DO UPDATE SET units = excluded.units",
                (quota_day(self._clock()), units),
            )

    # --- cache -----------------------------------------------------------------------

    def _cache_key(self, query: str) -> str:
        return f"{self.region}:{' '.join(query.lower().split())}"

    def _cached(self, key: str) -> list[Video] | None:
        row = self._conn.execute(
            "SELECT results, fetched_at FROM youtube_cache WHERE query_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if self._clock() - datetime.fromisoformat(row[1]) > CACHE_TTL:
            return None
        return [Video(**v) for v in json.loads(row[0])]

    def _store(self, key: str, videos: list[Video]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO youtube_cache (query_key, results, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT (query_key) DO UPDATE SET "
                "results = excluded.results, fetched_at = excluded.fetched_at",
                (key, json.dumps([asdict(v) for v in videos]), self._clock().isoformat()),
            )

    # --- search ----------------------------------------------------------------------

    async def search(self, query: str) -> list[Video]:
        """Top embeddable videos for ``query``. Raises ``YoutubeUnavailable`` with a spoken
        reason when there is no key, the daily budget is spent, or YouTube fails."""
        if self._client is None:
            raise YoutubeUnavailable(NOT_CONFIGURED_SAY)
        key = self._cache_key(query)
        cached = self._cached(key)
        if cached is not None:
            return cached
        used = self.quota_used()
        if used + SEARCH_COST > self.daily_budget:
            raise YoutubeUnavailable(QUOTA_SAY)
        # Count the search before making it: a failed request can still cost quota, and
        # erring high keeps us under YouTube's hard limit.
        self._set_quota(used + SEARCH_COST)
        try:
            videos = await self._client.search(query)
        except YoutubeQuotaError:
            self._set_quota(max(self.daily_budget, used + SEARCH_COST))
            raise YoutubeUnavailable(QUOTA_SAY) from None
        except YoutubeFetchError as exc:
            raise YoutubeUnavailable(FETCH_FAILED_SAY) from exc
        self._store(key, videos)
        return videos

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
