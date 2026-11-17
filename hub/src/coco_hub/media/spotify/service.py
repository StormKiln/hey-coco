"""What the Hub does with Spotify: target the iPad, search + rank, control, now-playing.

Audio plays in the Spotify iOS app on the iPad (a Spotify Connect device); the Hub only drives
it through the Web API. ``SpotifyService`` is registered as ``services["spotify"]`` whether or
not Spotify is configured: unconfigured (no ``spotify.client_id``) it never touches the
network and every call raises ``SpotifyNotConfigured`` ("Spotify isn't set up yet.").

Device targeting: the device named ``spotify.device_name`` (case-insensitive), else the
active device, else the only one. If it isn't in the device list, the ``DeviceWaker`` gets one
chance to bring it back (polling the list, #95) before Coco says "Open Spotify on the iPad
once, then try again." A play wake names the URI, which the Display's Spotify SDK may already
start while waking Spotify (#116); then ``play`` doesn't start it a second time.

Now playing: ``run()`` polls ``currently-playing`` and publishes a ``now_playing`` event when
it changes. It polls every 5 s for 10 minutes after any Spotify command, then every 60 s while
something is playing, and not at all otherwise (until the next command or login).
"""

import asyncio
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from coco_hub.events import EventBus
from coco_hub.media.spotify.auth import SpotifyAuth
from coco_hub.media.spotify.client import Device, Item, ItemKind, SpotifyClient
from coco_hub.media.spotify.errors import (
    SpotifyError,
    SpotifyNoDevice,
    SpotifyNotConfigured,
    SpotifyNotConnected,
    SpotifyRateLimited,
    SpotifyVolumeUnsupported,
)
from coco_hub.protocol import MusicKind, NowPlaying, NowPlayingEvent
from coco_hub.settings import Settings

log = logging.getLogger(__name__)

MUSIC_KINDS: tuple[MusicKind, ...] = ("artist", "album", "playlist", "song", "any")

ACTIVE_POLL_S = 5
PLAYING_POLL_S = 60
ACTIVE_WINDOW = timedelta(minutes=10)

_KIND_ORDER: dict[ItemKind, int] = {"artist": 0, "track": 1, "album": 2, "playlist": 3}
_SEARCH_KINDS: dict[MusicKind, tuple[ItemKind, ...]] = {
    "any": ("artist", "track", "album", "playlist"),
    "artist": ("artist",),
    "album": ("album",),
    "playlist": ("playlist",),
    "song": ("track",),
}


class DeviceWaker(Protocol):
    """Tries to make the iPad's Spotify app show up as a Connect device again.

    After trying, it calls ``appeared`` (which lists devices again) until that returns True,
    and returns True only then. Spotify drops an idle iPad from the device list; spike #5.
    ``play`` (#116): the wake is for playing ``uri`` (None: whatever Spotify last played), so
    the Display may start it while waking Spotify; without ``play`` it must not start music.
    """

    async def wake(
        self,
        device_name: str | None,
        appeared: Callable[[], Awaitable[bool]],
        *,
        play: bool = False,
        uri: str | None = None,
    ) -> bool: ...


class NoopWaker:
    """Does nothing (tests, and ``spotify.wake_wait_s = 0``). The Hub's real waker is
    ``coco_hub.media.spotify.waker.DisplayWaker``."""

    async def wake(
        self,
        device_name: str | None,
        appeared: Callable[[], Awaitable[bool]],
        *,
        play: bool = False,
        uri: str | None = None,
    ) -> bool:
        return False


# --- pure helpers ------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", folded)
    return words[1:] if words[:1] == ["the"] and len(words) > 1 else words


def rank(items: Sequence[Item], query: str) -> list[Item]:
    """Best first: exact name match > every query word in the name > the rest; then
    popularity; then artist > track > album > playlist; then Spotify's order."""
    wanted = _words(query)

    def score(item: Item) -> tuple[int, int, int]:
        name = _words(item.name)
        if name == wanted:
            tier = 2
        elif wanted and set(wanted) <= set(name):
            tier = 1
        else:
            tier = 0
        return (-tier, -(item.popularity or 0), _KIND_ORDER[item.kind])

    return sorted(items, key=score)


def choose_device(devices: Sequence[Device], name: str | None) -> Device | None:
    if name and name.strip():
        wanted = name.strip().casefold()
        return next((d for d in devices if d.name.strip().casefold() == wanted), None)
    active = next((d for d in devices if d.is_active), None)
    if active is not None:
        return active
    return devices[0] if len(devices) == 1 else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- service -----------------------------------------------------------------------------


class SpotifyService:
    def __init__(
        self,
        auth: SpotifyAuth,
        client: SpotifyClient,
        *,
        device_name: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        bus: EventBus | None = None,
        waker: DeviceWaker | None = None,
    ) -> None:
        self.auth = auth
        self._client = client
        self.device_name = device_name
        self._clock = clock
        self._bus = bus
        self._waker = waker or NoopWaker()
        self._now_playing: NowPlaying | None = None
        self._last_command: datetime | None = None
        self._backoff_s: float | None = None
        self._wake = asyncio.Event()

    @classmethod
    def from_settings(
        cls,
        conn: sqlite3.Connection,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utc_now,
        bus: EventBus | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        waker: DeviceWaker | None = None,
    ) -> "SpotifyService":
        cfg = settings.spotify
        scheme = "https" if settings.tls.enabled else "http"
        redirect = cfg.redirect_uri or f"{scheme}://127.0.0.1:{settings.port}/spotify/callback"
        auth = SpotifyAuth(
            conn, client_id=cfg.client_id, redirect_uri=redirect, clock=clock, transport=transport
        )
        client = SpotifyClient(auth, transport=transport, sleep=sleep)
        return cls(auth, client, device_name=cfg.device_name, clock=clock, bus=bus, waker=waker)

    # --- readiness ---------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self.auth.configured

    @property
    def logged_in(self) -> bool:
        return self.auth.logged_in

    @property
    def ready(self) -> bool:
        return self.configured and self.logged_in

    def ensure_ready(self) -> None:
        """Raise ``SpotifyNotConfigured`` / ``SpotifyNotConnected`` unless usable."""
        if not self.configured:
            raise SpotifyNotConfigured()
        if not self.logged_in:
            raise SpotifyNotConnected()

    async def complete_login(self, code: str, state: str) -> None:
        await self.auth.complete_login(code, state)
        self.touch()

    # --- devices -----------------------------------------------------------------------

    async def target_device(self) -> Device:
        device, _ = await self._target_device()
        return device

    async def _target_device(
        self, *, play: bool = False, uri: str | None = None
    ) -> tuple[Device, bool]:
        """The device to use, and whether it had to be woken first (the wake may already
        have started ``uri`` there, #116)."""
        device = choose_device(await self._client.devices(), self.device_name)
        woken = False
        if device is None:
            found: Device | None = None

            async def appeared() -> bool:
                nonlocal found
                found = choose_device(await self._client.devices(), self.device_name)
                return found is not None

            if await self._waker.wake(self.device_name, appeared, play=play, uri=uri):
                device, woken = found, True
        if device is None:
            raise SpotifyNoDevice(f"device {self.device_name or '(any)'} not visible")
        return device, woken

    async def _already_playing(self, device: Device, uri: str | None) -> bool:
        """True when ``device`` is playing ``uri`` (None: anything) right now: the Display's
        Spotify SDK wake started it (#116), so the Web API mustn't restart it."""
        state = await self._client.playback()
        if state is None or not state.is_playing or state.device_id != device.id:
            return False
        return uri is None or uri in (state.context_uri, state.item_uri)

    async def visible_device(self) -> Device | None:
        """The device we'd play on, if Spotify sees it now; never wakes one (diagnostics)."""
        self.ensure_ready()
        return choose_device(await self._client.devices(), self.device_name)

    # --- search + play -----------------------------------------------------------------

    async def search(self, query: str, kind: MusicKind) -> list[Item]:
        self.ensure_ready()
        items = await self._client.search(query, _SEARCH_KINDS[kind])
        return rank(items, query)

    async def play(self, item: Item | None, *, shuffle: bool = False) -> None:
        """Play ``item`` on the target device; None resumes whatever Spotify had there.
        ``shuffle`` turns Spotify's shuffle on once it is playing there (left as is
        otherwise)."""
        self.ensure_ready()
        uri = item.uri if item is not None else None
        device, woken = await self._target_device(play=True, uri=uri)
        if woken and await self._already_playing(device, uri):
            log.info("Spotify already plays %s on the iPad after the wake", uri or "something")
        elif item is None:
            await self._client.play(device.id)
        elif item.kind == "track":
            await self._client.play(device.id, uris=[item.uri])
        else:
            await self._client.play(device.id, context_uri=item.uri)
        if shuffle:
            await self._client.set_shuffle(True, device.id)
        self.touch()

    # --- transport ---------------------------------------------------------------------

    async def pause(self) -> None:
        self.ensure_ready()
        await self._client.pause()
        self.touch()

    stop = pause  # Connect has no "stop"; pausing is the closest.

    async def resume(self) -> None:
        await self.play(None)

    async def skip(self) -> None:
        self.ensure_ready()
        await self._client.next()
        self.touch()

    async def set_volume(self, level: int) -> None:
        self.ensure_ready()
        device = await self._volume_device()
        await self._client.set_volume(level, device.id)

    async def change_volume(self, delta: int) -> None:
        self.ensure_ready()
        device = await self._volume_device()
        current = device.volume_percent if device.volume_percent is not None else 50
        await self._client.set_volume(max(0, min(100, current + delta)), device.id)

    async def _volume_device(self) -> Device:
        device = await self.target_device()
        if not device.supports_volume:
            raise SpotifyVolumeUnsupported(f"{device.name} doesn't support remote volume")
        return device

    # --- now playing -------------------------------------------------------------------

    @property
    def now_playing(self) -> NowPlaying | None:
        """The last state seen (for the WS ``hello``)."""
        return self._now_playing

    async def currently_playing(self) -> NowPlaying | None:
        """Ask Spotify now (for "what's playing") and publish if it changed."""
        self.ensure_ready()
        current = await self._client.currently_playing()
        self._set_now_playing(current)
        return current

    def _set_now_playing(self, current: NowPlaying | None) -> None:
        if current == self._now_playing:
            return
        self._now_playing = current
        if self._bus is not None:
            self._bus.publish(NowPlayingEvent(server_time=self._clock(), now_playing=current))

    def touch(self) -> None:
        """A Spotify command just happened: poll now and often for a while."""
        self._last_command = self._clock()
        self._wake.set()

    def next_poll_delay(self) -> float | None:
        """Seconds until the next poll, or None to wait for ``touch()``."""
        if not self.ready:
            return None
        if self._backoff_s is not None:
            return self._backoff_s
        if self._last_command is not None and self._clock() - self._last_command < ACTIVE_WINDOW:
            return ACTIVE_POLL_S
        if self._now_playing is not None and self._now_playing.is_playing:
            return PLAYING_POLL_S
        return None

    async def poll_once(self) -> None:
        if not self.ready:
            return
        self._backoff_s = None
        try:
            current = await self._client.currently_playing()
        except SpotifyRateLimited as exc:
            self._backoff_s = max(exc.retry_after, ACTIVE_POLL_S)
            log.warning("Spotify rate limited the now-playing poll; waiting %gs", exc.retry_after)
            return
        except SpotifyError as exc:
            log.warning("Spotify now-playing poll failed: %s", exc)
            return
        self._set_now_playing(current)

    async def run(self) -> None:
        """Poll now-playing forever (cancel to stop). Start only when configured."""
        while True:
            self._wake.clear()  # a touch() during the poll still wakes the wait below
            await self.poll_once()
            delay = self.next_poll_delay()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def aclose(self) -> None:
        await self._client.aclose()
        await self.auth.aclose()
