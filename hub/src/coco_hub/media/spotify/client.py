"""Spotify Web API calls (https://developer.spotify.com/documentation/web-api).

Only what the Hub needs: search, the Connect device list, start/resume playback on a device,
pause, next, volume and currently-playing. Error handling:

- 401: refresh the access token once and retry;
- 429: wait ``Retry-After`` and retry once when it is short (≤ 3 s), else raise
  ``SpotifyRateLimited`` carrying the wait;
- 403 ``PREMIUM_REQUIRED``: ``SpotifyPremiumRequired``;
- 404 on ``/me/player/*`` ("No active device" / unknown device id): ``SpotifyNoDevice``;
- anything else ≥ 400, network failures and unreadable bodies: ``SpotifyError``.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from coco_hub.media.spotify.errors import (
    SpotifyError,
    SpotifyNoDevice,
    SpotifyPremiumRequired,
    SpotifyRateLimited,
)
from coco_hub.protocol import NowPlaying

API_URL = "https://api.spotify.com/v1"
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_INLINE_WAIT_S = 3.0
SEARCH_LIMIT = 5

ItemKind = Literal["artist", "album", "playlist", "track"]
_SECTIONS: tuple[tuple[str, ItemKind], ...] = (
    ("artists", "artist"),
    ("tracks", "track"),
    ("albums", "album"),
    ("playlists", "playlist"),
)


class TokenProvider(Protocol):
    async def access_token(self) -> str: ...

    async def refresh(self, stale: str | None = None) -> str: ...


@dataclass(frozen=True)
class Item:
    """A search result Coco can play."""

    kind: ItemKind
    uri: str
    name: str
    artist: str | None = None
    """First artist (tracks, albums) or the owner's name (playlists)."""
    popularity: int | None = None
    """0-100 for artists and tracks (albums when present); playlists have none."""


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    type: str
    is_active: bool
    volume_percent: int | None
    supports_volume: bool


@dataclass(frozen=True)
class Playback:
    """The parts of ``/me/player`` that say what is playing where."""

    device_id: str | None
    is_playing: bool
    context_uri: str | None
    """The artist / album / playlist being played, if any."""
    item_uri: str | None
    """The track (or episode)."""


# --- parsing -----------------------------------------------------------------------------


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_artist(raw: dict[str, Any]) -> str | None:
    artists = raw.get("artists")
    if isinstance(artists, list) and artists and isinstance(artists[0], dict):
        return _str(artists[0].get("name"))
    return None


def _parse_item(kind: ItemKind, raw: Any) -> Item | None:
    if not isinstance(raw, dict):
        return None  # Spotify sends null entries (notably for playlists)
    uri, name = _str(raw.get("uri")), _str(raw.get("name"))
    if uri is None or name is None:
        return None
    if kind == "playlist":
        owner = raw.get("owner")
        artist = _str(owner.get("display_name")) if isinstance(owner, dict) else None
    elif kind == "artist":
        artist = None
    else:
        artist = _first_artist(raw)
    popularity = raw.get("popularity")
    return Item(
        kind=kind,
        uri=uri,
        name=name.strip(),
        artist=artist,
        popularity=popularity if isinstance(popularity, int) else None,
    )


def parse_search(data: Any) -> list[Item]:
    """Items from a ``/search`` response: artists, tracks, albums, playlists, in API order."""
    items: list[Item] = []
    if not isinstance(data, dict):
        return items
    for section, kind in _SECTIONS:
        block = data.get(section)
        raw_items = block.get("items") if isinstance(block, dict) else None
        for raw in raw_items if isinstance(raw_items, list) else []:
            item = _parse_item(kind, raw)
            if item is not None:
                items.append(item)
    return items


def parse_devices(data: Any) -> list[Device]:
    """Connect devices that can be targeted (restricted devices without an id are dropped)."""
    devices: list[Device] = []
    for raw in data.get("devices", []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict) or not _str(raw.get("id")) or raw.get("is_restricted"):
            continue
        volume = raw.get("volume_percent")
        devices.append(
            Device(
                id=raw["id"],
                name=str(raw.get("name") or ""),
                type=str(raw.get("type") or ""),
                is_active=bool(raw.get("is_active")),
                volume_percent=volume if isinstance(volume, int) else None,
                supports_volume=bool(raw.get("supports_volume")),
            )
        )
    return devices


def parse_currently_playing(data: Any) -> NowPlaying | None:
    """``NowPlaying`` from ``/me/player/currently-playing``; None when there is no item."""
    if not isinstance(data, dict):
        return None
    item = data.get("item")
    if not isinstance(item, dict) or not _str(item.get("name")):
        return None
    if item.get("type") == "episode":
        show = item.get("show") if isinstance(item.get("show"), dict) else {}
        artist = _str(show.get("publisher")) or _str(show.get("name")) or ""
        album = _str(show.get("name"))
        images = item.get("images") or show.get("images")
    else:
        names = [
            a.get("name")
            for a in item.get("artists") or []
            if isinstance(a, dict) and a.get("name")
        ]
        artist = ", ".join(names)
        album_raw = item.get("album") if isinstance(item.get("album"), dict) else {}
        album = _str(album_raw.get("name"))
        images = album_raw.get("images")
    artwork = None
    if isinstance(images, list) and images and isinstance(images[0], dict):
        artwork = _str(images[0].get("url"))  # Spotify lists the largest first
    return NowPlaying(
        source="spotify",
        title=item["name"],
        artist=artist,
        album=album,
        artwork_url=artwork,
        is_playing=bool(data.get("is_playing")),
    )


def parse_playback(data: Any) -> Playback | None:
    """``Playback`` from ``/me/player``; None for an empty answer."""
    if not isinstance(data, dict):
        return None
    device = data.get("device") if isinstance(data.get("device"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    return Playback(
        device_id=_str(device.get("id")),
        is_playing=bool(data.get("is_playing")),
        context_uri=_str(context.get("uri")),
        item_uri=_str(item.get("uri")),
    )


def _error_reason(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json()["error"]
        return str(error.get("reason", "")), str(error.get("message", ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return "", ""


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "1")))
    except ValueError:
        return 1.0


# --- client ------------------------------------------------------------------------------


class SpotifyClient:
    def __init__(
        self,
        tokens: TokenProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        base_url: str = API_URL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_inline_wait_s: float = MAX_INLINE_WAIT_S,
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._max_wait = max_inline_wait_s
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        refreshed = waited = False
        while True:
            token = await self._tokens.access_token()
            try:
                response = await self._client().request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise SpotifyError(f"Spotify request failed: {type(exc).__name__}") from None
            status = response.status_code
            if status == 401 and not refreshed:
                refreshed = True
                await self._tokens.refresh(stale=token)
                continue
            if status == 429:
                wait = _retry_after(response)
                if not waited and wait <= self._max_wait:
                    waited = True
                    await self._sleep(wait)
                    continue
                raise SpotifyRateLimited(wait)
            if status < 400:
                return response
            reason, message = _error_reason(response)
            if status == 403 and (reason == "PREMIUM_REQUIRED" or "premium" in message.lower()):
                raise SpotifyPremiumRequired(message or "Premium required")
            if status == 404 and path.startswith("/me/player"):
                raise SpotifyNoDevice(message or "no active device")
            raise SpotifyError(f"Spotify answered HTTP {status} {reason or message}".rstrip())

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise SpotifyError("Spotify sent an unreadable response") from exc

    # --- endpoints ---------------------------------------------------------------------

    async def search(
        self, query: str, kinds: Sequence[ItemKind], limit: int = SEARCH_LIMIT
    ) -> list[Item]:
        response = await self._request(
            "GET", "/search", params={"q": query, "type": ",".join(kinds), "limit": limit}
        )
        return parse_search(self._json(response))

    async def devices(self) -> list[Device]:
        return parse_devices(self._json(await self._request("GET", "/me/player/devices")))

    async def play(
        self,
        device_id: str,
        *,
        context_uri: str | None = None,
        uris: Sequence[str] | None = None,
    ) -> None:
        """Start ``context_uri`` (artist/album/playlist) or ``uris`` (tracks) on the device,
        transferring playback to it; with neither, resume there."""
        body: dict[str, Any] | None = None
        if context_uri is not None:
            body = {"context_uri": context_uri}
        elif uris is not None:
            body = {"uris": list(uris)}
        await self._request("PUT", "/me/player/play", params={"device_id": device_id}, json=body)

    async def pause(self, device_id: str | None = None) -> None:
        await self._request("PUT", "/me/player/pause", params=_device(device_id))

    async def next(self, device_id: str | None = None) -> None:
        await self._request("POST", "/me/player/next", params=_device(device_id))

    async def set_volume(self, percent: int, device_id: str | None = None) -> None:
        params: dict[str, str | int] = {"volume_percent": max(0, min(100, percent))}
        params.update(_device(device_id))
        await self._request("PUT", "/me/player/volume", params=params)

    async def set_shuffle(self, state: bool, device_id: str | None = None) -> None:
        params: dict[str, str | int] = {"state": "true" if state else "false"}
        params.update(_device(device_id))
        await self._request("PUT", "/me/player/shuffle", params=params)

    async def currently_playing(self) -> NowPlaying | None:
        response = await self._request(
            "GET", "/me/player/currently-playing", params={"additional_types": "episode"}
        )
        if response.status_code == 204 or not response.content:
            return None
        return parse_currently_playing(self._json(response))

    async def playback(self) -> Playback | None:
        """What plays where (``/me/player``); None when nothing is (204)."""
        response = await self._request("GET", "/me/player", params={"additional_types": "episode"})
        if response.status_code == 204 or not response.content:
            return None
        return parse_playback(self._json(response))

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


def _device(device_id: str | None) -> dict[str, str | int]:
    return {"device_id": device_id} if device_id else {}
