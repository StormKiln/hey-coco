"""Wikimedia Commons picture search (MediaWiki API, no key).

One request: ``generator=search`` over the File namespace (6) with ``prop=imageinfo`` for the
URL, size, MIME type, a 1280 px thumbnail and the licence/author metadata. We keep bitmap
photos (JPEG/PNG/WebP) that are big enough, carry a reusable licence (CC0, public domain,
CC BY, CC BY-SA — no NC/ND/GFDL-only) and don't look like a diagram, map or drawing.

Image URLs point straight at Commons media hosts (public, no token): the Display loads
them directly.
"""

import html
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from coco_hub.protocol import Image

API_URL = "https://commons.wikimedia.org/w/api.php"
# Identifies the software only — never anything about the household.
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_IMAGES = 8
THUMB_WIDTH = 1280
SEARCH_LIMIT = 30
MIN_SHORT_SIDE = 480
MIN_LONG_SIDE = 640
MAX_ASPECT = 3.0
MAX_AUTHOR = 120
MAX_QUERY = 80

_MIMES = {"image/jpeg", "image/png", "image/webp"}
# Licence codes from the `License` extmetadata field.
_LICENSE_CODE = re.compile(r"^(?:cc0|pd(?:-.*)?|cc-by-\d.*|cc-by-sa-\d.*)$")
_LICENSE_SHORT = re.compile(r"^(?:CC0(?: 1\.0)?|Public domain|CC BY \d.*|CC BY-SA \d.*)$", re.I)
# Not what a kid asking "show me a …" wants to see. Matched as whole words against the file
# title and its categories, unless the query itself asks for it ("a map of Canada").
_NOT_A_PHOTO = (
    "diagram",
    "diagrams",
    "map",
    "maps",
    "drawing",
    "drawings",
    "illustration",
    "illustrations",
    "chart",
    "charts",
    "logo",
    "logos",
    "coat of arms",
    "flag",
    "flags",
    "schematic",
    "schematics",
    "sketch",
    "sketches",
    "blueprint",
    "blueprints",
    "plan",
    "plans",
    "stamp",
    "stamps",
    "x-ray",
    "x-rays",
)
# Never shown, and a query containing one of these isn't searched at all.
_UNSUITABLE = (
    "nude",
    "nudes",
    "nudity",
    "naked",
    "sex",
    "sexual",
    "erotic",
    "erotica",
    "porn",
    "pornography",
    "genitalia",
    "penis",
    "vagina",
    "topless",
    "corpse",
    "corpses",
    "dead bodies",
    "gore",
    "execution",
    "executions",
    "lynching",
    "torture",
)
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")
_QUERY_JUNK = re.compile(r"[^\w\s'-]+")
_EXTENSION = re.compile(r"\.(?:jpe?g|png|webp)$", re.I)


def _words_re(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b", re.I)


_NOT_A_PHOTO_RE = _words_re(_NOT_A_PHOTO)
_UNSUITABLE_RE = _words_re(_UNSUITABLE)


class PictureFetchError(Exception):
    """Commons couldn't be reached or answered with something we don't understand."""


def strip_html(text: str) -> str:
    """Drop tags, decode entities, collapse whitespace."""
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()


def license_allowed(code: str | None, short: str | None) -> bool:
    """Reusable for a kitchen display: CC0, public domain, CC BY or CC BY-SA (any version)."""
    if code:
        return bool(_LICENSE_CODE.match(code.strip().lower()))
    if short:
        return bool(_LICENSE_SHORT.match(strip_html(short)))
    return False


def _license_label(code: str, short: str | None) -> str:
    if short and (label := strip_html(short)):
        return label
    code = code.lower()
    if code == "cc0":
        return "CC0"
    if code.startswith("pd"):
        return "Public domain"
    m = re.match(r"cc-by(-sa)?-([\d.]+)", code)
    if m:
        return f"CC BY{'-SA' if m[1] else ''} {m[2]}"
    return code


def clean_query(query: str) -> str:
    """Lowercase, no search syntax (quotes, colons, slashes…), single spaces, bounded."""
    return _SPACE.sub(" ", _QUERY_JUNK.sub(" ", query.lower())).strip()[:MAX_QUERY].strip()


def search_terms(query: str, *, phrase: bool = True) -> str:
    """CirrusSearch terms: the cleaned query (as a phrase by default), bitmaps only."""
    q = clean_query(query)
    return f'"{q}" filetype:bitmap' if phrase else f"{q} filetype:bitmap"


def is_unsuitable(query: str) -> bool:
    return bool(_UNSUITABLE_RE.search(query))


def _strip_tracking(url: str) -> str:
    """Drop the ``utm_*`` parameters Commons appends to upload URLs."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _meta(info: dict[str, Any], key: str) -> str | None:
    value = info.get("extmetadata", {}).get(key, {}).get("value")
    return value if isinstance(value, str) else None


def _title(page_title: str, object_name: str | None) -> str:
    if object_name and (name := strip_html(object_name)):
        return name
    name = page_title.removeprefix("File:").replace("_", " ")
    return _EXTENSION.sub("", name).strip()


def _image(page: dict[str, Any], query: str) -> tuple[int, int, Image] | None:
    """(rank group, search rank, Image) for a usable page, else None."""
    infos = page.get("imageinfo")
    if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    width, height = info.get("width"), info.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        return None
    mime = info.get("mime")
    if mime not in _MIMES:
        return None
    if min(width, height) < MIN_SHORT_SIDE or max(width, height) < MIN_LONG_SIDE:
        return None
    if max(width, height) / min(width, height) > MAX_ASPECT:
        return None
    code, short = _meta(info, "License"), _meta(info, "LicenseShortName")
    if not license_allowed(code, short):
        return None
    page_title = str(page.get("title", ""))
    described = f"{page_title} {strip_html(_meta(info, 'Categories') or '')}"
    if _UNSUITABLE_RE.search(described):
        return None
    if _NOT_A_PHOTO_RE.search(described) and not _NOT_A_PHOTO_RE.search(query):
        return None
    url, source = info.get("url"), info.get("descriptionurl")
    if not isinstance(url, str) or not isinstance(source, str):
        return None
    thumb = info.get("thumburl") if isinstance(info.get("thumburl"), str) else url
    author = strip_html(_meta(info, "Artist") or "")[:MAX_AUTHOR].strip() or None
    image = Image(
        url=_strip_tracking(url),
        thumb_url=_strip_tracking(thumb),
        title=_title(page_title, _meta(info, "ObjectName")),
        width=width,
        height=height,
        license=_license_label(code or "", short),
        author=author,
        source_url=source,
    )
    rank = page.get("index")
    # PNGs on Commons are more often diagrams and screenshots: photos first.
    return (1 if mime == "image/png" else 0, rank if isinstance(rank, int) else 10**6, image)


def parse_results(data: Any, *, query: str, limit: int = MAX_IMAGES) -> list[Image]:
    """Usable images from an API response, best first, at most ``limit``."""
    pages = data.get("query", {}).get("pages", []) if isinstance(data, dict) else []
    if not isinstance(pages, list):
        return []
    q = clean_query(query)
    found = [hit for p in pages if isinstance(p, dict) and (hit := _image(p, q))]
    found.sort(key=lambda hit: (hit[0], hit[1]))
    return [image for _, _, image in found[:limit]]


class CommonsClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        url: str = API_URL,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._url = url

    async def search(self, query: str, *, limit: int = MAX_IMAGES) -> list[Image]:
        """Up to ``limit`` usable images for ``query``; [] when none. Raises PictureFetchError.

        Searches the exact phrase first and, for a multi-word query that finds nothing,
        the words loosely.
        """
        q = clean_query(query)
        if not q or is_unsuitable(q):
            return []
        # A client per search: searches are rare, and there's no pool to close on shutdown.
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT},
        ) as http:
            images = await self._fetch(http, search_terms(q), q, limit)
            if not images and " " in q:
                images = await self._fetch(http, search_terms(q, phrase=False), q, limit)
        return images

    async def _fetch(
        self, http: httpx.AsyncClient, terms: str, query: str, limit: int
    ) -> list[Image]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": terms,
            "gsrlimit": str(SEARCH_LIMIT),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(THUMB_WIDTH),
            "iiextmetadatafilter": "LicenseShortName|License|Artist|ObjectName|Categories",
        }
        try:
            response = await http.get(self._url, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PictureFetchError(f"Commons request failed: {exc}") from exc
        if not isinstance(data, dict) or "error" in data:
            raise PictureFetchError(f"Commons API error: {str(data)[:200]}")
        return parse_results(data, query=query, limit=limit)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PictureResult:
    """The pictures on screen: ``current`` is ``images`` rotated so the shown one is first."""

    query: str
    images: tuple[Image, ...]
    position: int = 0

    @property
    def current(self) -> list[Image]:
        return list(self.images[self.position :] + self.images[: self.position])


class PictureService:
    """Commons search with a day-long in-memory cache, and the last result shown.

    One Display at a time, so "the last result" is per Hub.
    """

    def __init__(
        self,
        client: CommonsClient | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        ttl: timedelta = timedelta(days=1),
        max_entries: int = 256,
    ) -> None:
        self._client = client or CommonsClient()
        self._clock = clock
        self._ttl = ttl
        self._max = max_entries
        self._cache: OrderedDict[str, tuple[datetime, tuple[Image, ...]]] = OrderedDict()
        self.last: PictureResult | None = None

    async def search(self, query: str) -> list[Image]:
        """Images for ``query`` (cached, including "nothing found"). Raises PictureFetchError."""
        key = clean_query(query)
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            self._cache.move_to_end(key)
            return list(hit[1])
        images = tuple(await self._client.search(key))
        self._cache[key] = (now, images)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return list(images)

    def remember(self, query: str, images: list[Image]) -> PictureResult:
        self.last = PictureResult(query=query, images=tuple(images))
        return self.last

    def advance(self) -> PictureResult | None:
        """Move to the next picture (wrapping); None if there's nothing to move to."""
        if self.last is None or len(self.last.images) < 2:
            return None
        last = self.last
        self.last = PictureResult(last.query, last.images, (last.position + 1) % len(last.images))
        return self.last
