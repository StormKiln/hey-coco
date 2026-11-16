"""What Coco heard and did, and whether its services are up: ``GET /v1/diagnostics``.

- Every ``/v1/utterance`` call is traced (``record``): kept in an in-memory ring buffer (last
  100, never persisted) and logged at INFO on ``coco_hub.utterance``. Transcripts are
  household speech; with ``diagnostics.log_transcripts = false`` the log line carries no
  ``text`` / ``say`` (the in-memory buffer still does, for the diagnostics screen).
- Health checks are best-effort: each runs concurrently under ``check_timeout_s`` and a
  failure or timeout becomes ``status: "error"`` instead of failing the endpoint.
- Nothing here exposes a secret: settings are summarised as booleans and names only.
"""

import asyncio
import logging
import os
import subprocess
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import cache
from importlib import metadata
from typing import Any, TypeVar

from fastapi import APIRouter, Request

from coco_hub.protocol import (
    Diagnostics,
    DiagnosticsHealth,
    DiagnosticsSettings,
    Night,
    OllamaHealth,
    QwenSummary,
    SpotifyHealth,
    SpotifySummary,
    UtteranceTrace,
    WeatherHealth,
    YoutubeHealth,
)
from coco_hub.settings import HUB_DIR, Settings

log = logging.getLogger("coco_hub.utterance")

TRACE_BUFFER_SIZE = 100
TRACES_IN_RESPONSE = 20
CHECK_TIMEOUT_S = 1.5
WEATHER_STALE_AFTER = timedelta(minutes=30)

T = TypeVar("T")


class TraceBuffer:
    """The last ``maxlen`` utterance traces, in memory only."""

    def __init__(self, maxlen: int = TRACE_BUFFER_SIZE) -> None:
        self._traces: deque[UtteranceTrace] = deque(maxlen=maxlen)

    def append(self, trace: UtteranceTrace) -> None:
        self._traces.append(trace)

    def recent(self, n: int) -> list[UtteranceTrace]:
        """Up to ``n`` traces, newest first."""
        return list(reversed(self._traces))[:n]

    def __len__(self) -> int:
        return len(self._traces)


@cache
def hub_version() -> str:
    try:
        return metadata.version("coco-hub")
    except metadata.PackageNotFoundError:
        return "unknown"


@cache
def git_sha() -> str | None:
    """``$COCO_GIT_SHA`` if set (e.g. stamped at deploy), else ``git rev-parse --short HEAD``
    in the checkout the Hub runs from. Best-effort: ``None`` on any failure."""
    if sha := os.environ.get("COCO_GIT_SHA", "").strip():
        return sha[:40]
    try:
        out = subprocess.run(
            ["git", "-C", str(HUB_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _hhmm(value: Any) -> str:
    return value.strftime("%H:%M")


def settings_summary(settings: Settings, services: dict[str, Any]) -> DiagnosticsSettings:
    spotify = services.get("spotify")
    youtube = services.get("youtube")
    loc = settings.location
    weather = services.get("weather")
    # The weather service also knows a home location set from the Display (#96).
    location_set = (
        weather.configured if weather is not None else loc.lat is not None and loc.lon is not None
    )
    return DiagnosticsSettings(
        timezone=settings.timezone,
        units=settings.units,
        location_set=location_set,
        qwen=QwenSummary(enabled=settings.qwen.enabled, model=settings.qwen.model),
        youtube_configured=bool(youtube is not None and youtube.configured),
        spotify=SpotifySummary(
            configured=bool(spotify is not None and spotify.configured),
            logged_in=bool(spotify is not None and spotify.configured and spotify.logged_in),
        ),
        night=Night(
            start=_hhmm(settings.night.start),
            end=_hhmm(settings.night.end),
            dark_after_idle_s=settings.night.dark_after_idle_s,
        ),
        log_transcripts=settings.diagnostics.log_transcripts,
    )


class DiagnosticsService:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime],
        check_timeout_s: float = CHECK_TIMEOUT_S,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.check_timeout_s = check_timeout_s
        self.traces = TraceBuffer()
        self.started_at = clock()
        self._started_monotonic = time.monotonic()

    # --- utterance traces ----------------------------------------------------------------

    def record(self, trace: UtteranceTrace) -> None:
        self.traces.append(trace)
        fields = trace.model_dump(mode="json")
        outcome = f"{trace.intent} ({trace.matched_by}) {trace.latency_ms}ms"
        if trace.error:
            outcome += f" error={trace.error}"
        if self.settings.diagnostics.log_transcripts:
            log.info("utterance %r -> %s", trace.text, outcome, extra={"trace": fields})
        else:
            fields.pop("text")
            fields.pop("say")
            log.info("utterance -> %s", outcome, extra={"trace": fields})

    # --- health --------------------------------------------------------------------------

    async def _guard(self, check: Awaitable[T], on_error: Callable[[str], T], name: str) -> T:
        try:
            return await asyncio.wait_for(check, timeout=self.check_timeout_s)
        except TimeoutError:
            return on_error("timed out")
        except Exception as exc:  # best-effort: never fail the endpoint
            logging.getLogger(__name__).warning(
                "diagnostics: %s check failed: %s", name, type(exc).__name__
            )
            return on_error(type(exc).__name__)

    async def _ollama(self, qwen: Any) -> OllamaHealth:
        if qwen is None:
            return OllamaHealth(
                status="off", reachable=False, model_present=False, model_loaded=False, detail=None
            )
        health = await qwen.client.health(timeout=self.check_timeout_s)
        if not health.reachable:
            status, detail = "error", health.error or "unreachable"
        elif not health.model_present:
            status, detail = "error", f"model {qwen.client.model} not pulled"
        elif not health.model_loaded:
            status, detail = "warn", "model not loaded (first answer will be slow)"
        else:
            status, detail = "ok", None
        return OllamaHealth(
            status=status,
            reachable=health.reachable,
            model_present=health.model_present,
            model_loaded=health.model_loaded,
            detail=detail,
        )

    async def _weather(self, weather: Any) -> WeatherHealth:
        if weather is None or not weather.configured:
            return WeatherHealth(status="off", fetched_at=None, stale=False, detail=None)
        snap = weather.snapshot
        failed = weather.last_failed_at is not None
        if snap is None:
            detail = "last fetch failed" if failed else "not fetched yet"
            return WeatherHealth(
                status="error" if failed else "warn", fetched_at=None, stale=False, detail=detail
            )
        stale = snap.stale or self.clock() - snap.fetched_at > WEATHER_STALE_AFTER
        return WeatherHealth(
            status="warn" if stale else "ok",
            fetched_at=snap.fetched_at,
            stale=stale,
            detail="last fetch failed" if failed else ("stale" if stale else None),
        )

    async def _youtube(self, youtube: Any) -> YoutubeHealth:
        if youtube is None or not youtube.configured:
            limit = self.settings.youtube.daily_quota
            return YoutubeHealth(status="off", quota_used=0, quota_limit=limit, detail=None)
        used = youtube.quota_used()
        limit = youtube.daily_budget
        exhausted = used >= limit
        return YoutubeHealth(
            status="warn" if exhausted else "ok",
            quota_used=used,
            quota_limit=limit,
            detail="daily quota used up" if exhausted else None,
        )

    async def _spotify(self, spotify: Any) -> SpotifyHealth:
        name = getattr(spotify, "device_name", None)
        if spotify is None or not spotify.configured:
            return SpotifyHealth(status="off", device_visible=False, device_name=name, detail=None)
        if not spotify.logged_in:
            return SpotifyHealth(
                status="warn", device_visible=False, device_name=name, detail="not logged in"
            )
        device = await spotify.visible_device()
        if device is None:
            return SpotifyHealth(
                status="warn", device_visible=False, device_name=name, detail="device not visible"
            )
        return SpotifyHealth(status="ok", device_visible=True, device_name=device.name, detail=None)

    async def health(self, state: Any) -> DiagnosticsHealth:
        services = state.services

        def ollama_err(detail: str) -> OllamaHealth:
            return OllamaHealth(
                status="error",
                reachable=False,
                model_present=False,
                model_loaded=False,
                detail=detail,
            )

        def weather_err(detail: str) -> WeatherHealth:
            return WeatherHealth(status="error", fetched_at=None, stale=False, detail=detail)

        def youtube_err(detail: str) -> YoutubeHealth:
            limit = self.settings.youtube.daily_quota
            return YoutubeHealth(status="error", quota_used=0, quota_limit=limit, detail=detail)

        def spotify_err(detail: str) -> SpotifyHealth:
            name = self.settings.spotify.device_name
            return SpotifyHealth(
                status="error", device_visible=False, device_name=name, detail=detail
            )

        ollama, weather, youtube, spotify = await asyncio.gather(
            self._guard(self._ollama(state.qwen), ollama_err, "ollama"),
            self._guard(self._weather(services.get("weather")), weather_err, "weather"),
            self._guard(self._youtube(services.get("youtube")), youtube_err, "youtube"),
            self._guard(self._spotify(services.get("spotify")), spotify_err, "spotify"),
        )
        return DiagnosticsHealth(ollama=ollama, weather=weather, youtube=youtube, spotify=spotify)

    async def snapshot(self, state: Any) -> Diagnostics:
        return Diagnostics(
            server_time=self.clock(),
            version=hub_version(),
            git_sha=git_sha(),
            started_at=self.started_at,
            uptime_s=int(time.monotonic() - self._started_monotonic),
            settings=settings_summary(self.settings, state.services),
            health=await self.health(state),
            utterances=self.traces.recent(TRACES_IN_RESPONSE),
        )


router = APIRouter()


@router.get("/diagnostics", response_model=Diagnostics)
async def diagnostics(request: Request) -> Diagnostics:
    service: DiagnosticsService = request.app.state.diagnostics
    return await service.snapshot(request.app.state)
