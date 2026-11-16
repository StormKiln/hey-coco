"""Places the person names ("what's the forecast in Portland"), via Open-Meteo geocoding.

Open-Meteo's search matches a place *name* only: "Portland Oregon" finds nothing. So
``resolve`` searches the whole phrase first and, when that finds nothing, shorter leading
words with the rest as a qualifier ("portland" + "oregon"). A comma splits name and
qualifier directly (Qwen fills ``Portland, Oregon``).

Choosing among results (``choose``):
- a qualifier picks the most populous result whose state/province (``admin1``), country or
  country code matches it;
- otherwise the most populous result. That pick is *ambiguous* when another result with the
  same name has at least ``RIVAL_SHARE`` of its population (Portland, Oregon vs Portland,
  Maine; not Paris, France vs Paris, Texas), and the answer then says which one it picked.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx

from coco_hub.weather.client import DEFAULT_TIMEOUT, USER_AGENT, WeatherFetchError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_RESULTS = 10
RIVAL_SHARE = 0.1
"""Another same-named place this big, relative to the pick, makes a bare name ambiguous."""

# Countries whose places are usually named with their state or province.
_BY_REGION = frozenset({"US", "CA", "AU"})
_COUNTRY_ALIASES = {
    "us": "US",
    "usa": "US",
    "america": "US",
    "united states": "US",
    "uk": "GB",
    "britain": "GB",
    "great britain": "GB",
}


def _key(text: str) -> str:
    """Lowercase ASCII words: ``Île-de-France`` -> ``ile de france``."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    timezone: str
    country: str = ""
    country_code: str = ""
    admin1: str = ""
    population: int = 0

    @property
    def label(self) -> str:
        """How to say it: ``Portland, Oregon``, ``Paris, France``."""
        region = self.admin1 if self.country_code in _BY_REGION else self.country
        region = region or self.admin1
        return f"{self.name}, {region}" if region and region != self.name else self.name

    def matches(self, qualifier: str) -> bool:
        q = _key(qualifier)
        if not q:
            return False
        if _COUNTRY_ALIASES.get(q) == self.country_code:
            return True
        return q in {_key(self.admin1), _key(self.country), self.country_code.lower()}


@dataclass(frozen=True)
class Pick:
    place: Place
    ambiguous: bool


def parse_results(data: Any) -> list[Place]:
    """Places in an Open-Meteo geocoding response (no ``results`` key: none). Results
    missing coordinates or a timezone are skipped."""
    if not isinstance(data, dict):
        raise WeatherFetchError("unexpected geocoding response")
    places = []
    for r in data.get("results") or []:
        try:
            places.append(
                Place(
                    name=str(r["name"]),
                    lat=float(r["latitude"]),
                    lon=float(r["longitude"]),
                    timezone=str(r["timezone"]),
                    country=str(r.get("country") or ""),
                    country_code=str(r.get("country_code") or ""),
                    admin1=str(r.get("admin1") or ""),
                    population=int(r.get("population") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return places


def choose(places: list[Place], qualifier: str | None) -> Pick | None:
    if not places:
        return None
    if qualifier:
        matching = [p for p in places if p.matches(qualifier)]
        if matching:
            return Pick(max(matching, key=lambda p: p.population), ambiguous=False)
    best = max(places, key=lambda p: p.population)
    rivals = [
        p
        for p in places
        if p is not best
        and _key(p.name) == _key(best.name)
        and p.population >= RIVAL_SHARE * best.population
    ]
    # A qualifier that matched nothing: say which one was picked.
    return Pick(best, ambiguous=bool(rivals) or bool(qualifier))


def _searches(query: str) -> list[tuple[str, str | None]]:
    """(name to search, qualifier) to try in order."""
    if "," in query:
        name, _, qualifier = query.partition(",")
        return [(name.strip(), qualifier.strip() or None)]
    words = query.split()
    return [(query, None)] + [
        (" ".join(words[:i]), " ".join(words[i:])) for i in range(len(words) - 1, 0, -1)
    ]


class Geocoder:
    """Open-Meteo geocoding, with answers (including "not found") cached for the Hub's life."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        url: str = GEOCODING_URL,
        max_cached: int = 64,
    ) -> None:
        self._url = url
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[str, Pick | None] = {}
        self._max_cached = max_cached

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    async def search(self, name: str) -> list[Place]:
        params: dict[str, str | int] = {
            "name": name,
            "count": MAX_RESULTS,
            "language": "en",
            "format": "json",
        }
        try:
            response = await self._client().get(self._url, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherFetchError(f"geocoding request failed: {exc}") from exc
        return parse_results(data)

    async def resolve(self, query: str) -> Pick | None:
        """The place ``query`` names, or None if nothing matches. Raises
        ``WeatherFetchError`` when the geocoding service can't be reached."""
        key = _key(query)
        if key in self._cache:
            return self._cache[key]
        pick = None
        for name, qualifier in _searches(query.strip()):
            pick = choose(await self.search(name), qualifier)
            if pick is not None:
                break
        if len(self._cache) >= self._max_cached:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = pick
        return pick

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
