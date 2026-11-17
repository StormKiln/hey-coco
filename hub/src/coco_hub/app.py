"""Coco Hub FastAPI app."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, FastAPI

from coco_hub import db, diagnostics, discovery, events, home, identity
from coco_hub.auth import require_token
from coco_hub.calendar.service import CalendarService
from coco_hub.devices.backend import DeviceBackend
from coco_hub.devices.service import DeviceService
from coco_hub.errors import install_error_handlers
from coco_hub.intents import build_registry
from coco_hub.intents.engine import RulesEngine, no_fallback
from coco_hub.logging_setup import configure_logging
from coco_hub.media.policy import AllowAllPolicy
from coco_hub.media.spotify import auth as spotify_auth
from coco_hub.media.spotify.service import SpotifyService
from coco_hub.media.spotify.waker import DisplayWaker
from coco_hub.media.youtube import YoutubeService
from coco_hub.pictures.commons import PictureService
from coco_hub.protocol import Health
from coco_hub.qwen.client import OllamaClient
from coco_hub.qwen.fallback import QwenFallback
from coco_hub.routes import idle, location, pair, utterance
from coco_hub.settings import Settings, load_settings
from coco_hub.sounds import SoundLibrary, media_router
from coco_hub.timers import routes as timer_routes
from coco_hub.timers.service import TimerService
from coco_hub.weather.service import WeatherService


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = _utc_now,
    weather: WeatherService | None = None,
    calendar: CalendarService | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    spotify_transport: httpx.AsyncBaseTransport | None = None,
    device_backends: Mapping[str, DeviceBackend] | None = None,
) -> FastAPI:
    weather = weather or WeatherService(settings, clock=clock)
    # Built here so bad [[devices]] config fails startup loudly; connects in the lifespan.
    devices = DeviceService.from_settings(settings, backends=device_backends)
    calendar = calendar or CalendarService(settings, clock=clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db.open_db(settings.data_dir)
        # A home location set from the Display (#96) overrides the config file's.
        if (saved_home := home.load(app.state.db)) is not None:
            weather.set_home(saved_home)
        calendar.attach_db(app.state.db)
        # Only with [[calendars]] configured: the idle screen never waits on this (#139).
        calendar_task = (
            asyncio.create_task(calendar.run(), name="calendar-refresh")
            if calendar.configured
            else None
        )
        timers = TimerService(
            app.state.db,
            clock=clock,
            bus=app.state.events,
            ring_cap_s=settings.timers.ring_cap_s,
            missed_grace_s=settings.timers.missed_grace_s,
        )
        app.state.services["timers"] = timers
        youtube = YoutubeService.from_settings(app.state.db, settings, clock=clock)
        app.state.services["youtube"] = youtube
        # Always registered; inert (no network, no poller) without spotify.client_id.
        # A missing iPad: ask the Display to open Spotify (#5; a no-op under Guided Access).
        waker = DisplayWaker(
            app.state.events,
            wait_s=settings.spotify.wake_wait_s,
            poll_s=settings.spotify.wake_poll_s,
            clock=clock,
            # Public (not a secret): the Display's Spotify iOS SDK needs it (#116).
            client_id=settings.spotify.client_id,
        )
        spotify = SpotifyService.from_settings(
            app.state.db,
            settings,
            clock=clock,
            bus=app.state.events,
            transport=spotify_transport,
            waker=waker,
        )
        app.state.services["spotify"] = spotify
        spotify_task = None
        if spotify.configured:
            spotify_task = asyncio.create_task(spotify.run(), name="spotify-now-playing")
        # The first tick also settles timers that ended while the Hub was down.
        timer_task = asyncio.create_task(timers.run(), name="timers")
        # In the background: an unreachable plug must never delay startup (#133).
        devices_task = asyncio.create_task(devices.serve(), name="devices")
        advertiser = None
        weather_task = None
        qwen: QwenFallback | None = app.state.qwen
        try:
            if settings.discovery.enabled:
                advertiser = discovery.Advertiser(settings, app.state.identity)
                await advertiser.start()
            # Always running: the Display can set a home location later (#96); until
            # then each refresh returns at once without touching the network.
            weather_task = asyncio.create_task(weather.run(), name="weather-refresh")
            if qwen is not None:
                qwen.schedule_warm_up()  # in the background; failures are logged
            yield
        finally:
            if qwen is not None:
                await qwen.aclose()
            if weather_task is not None:
                weather_task.cancel()
                with suppress(asyncio.CancelledError):
                    await weather_task
            await weather.aclose()
            if calendar_task is not None:
                calendar_task.cancel()
                with suppress(asyncio.CancelledError):
                    await calendar_task
            await calendar.aclose()
            if advertiser is not None:
                await advertiser.stop()
            timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer_task
            app.state.services.pop("timers", None)
            devices_task.cancel()
            with suppress(asyncio.CancelledError):
                await devices_task
            await devices.aclose()
            if spotify_task is not None:
                spotify_task.cancel()
                with suppress(asyncio.CancelledError):
                    await spotify_task
            await spotify.aclose()
            app.state.services.pop("spotify", None)
            await youtube.aclose()
            app.state.services.pop("youtube", None)
            app.state.db.close()

    app = FastAPI(title="Coco Hub", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    # Intent pipeline. Feature modules register intents into `app.state.intents` (see
    # coco_hub.intents.INTENT_MODULES), put their services in `app.state.services` (handed to
    # handlers as `ctx.services`), and the Qwen fallback replaces `app.state.fallback`.
    app.state.intents = build_registry(device_names=devices.spoken_names)
    app.state.engine = RulesEngine(app.state.intents)
    app.state.services = {"weather": weather, "devices": devices, "calendar": calendar}
    app.state.fallback = no_fallback
    app.state.qwen = None
    if settings.qwen.enabled:
        app.state.qwen = QwenFallback(
            OllamaClient(settings.qwen.base_url, settings.qwen.model, transport=ollama_transport),
            app.state.engine,
            timeout_s=settings.qwen.timeout_s,
            log_transcripts=settings.diagnostics.log_transcripts,
        )
        app.state.fallback = app.state.qwen
    app.state.sounds = SoundLibrary.load(settings.sounds_dir)
    app.state.services["sounds"] = app.state.sounds
    # The one media choke point (parental controls go here; see coco_hub.media.policy).
    app.state.services["media_policy"] = AllowAllPolicy()
    app.state.services["pictures"] = PictureService(clock=clock)
    app.state.events = events.EventBus()
    app.state.diagnostics = diagnostics.DiagnosticsService(settings, clock=clock)
    install_error_handlers(app)

    # Created here too (not only in __main__), so healthz and the Bonjour TXT record always
    # describe the certificate uvicorn serves: both read the same files in data_dir.
    hub_identity = identity.load_or_create(settings.data_dir)
    app.state.identity = hub_identity
    health = Health(
        status="ok",
        hub_id=hub_identity.hub_id,
        fingerprint=hub_identity.short_fingerprint if settings.tls.enabled else None,
    )

    @app.get("/healthz")
    def healthz() -> Health:
        return health

    # Every HTTP route under /v1 requires the bearer token. WebSocket routes must call
    # auth.authorize_websocket themselves (header dependencies don't apply to WS).
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])

    @v1.get("/ping")
    def ping() -> dict[str, bool]:
        return {"pong": True}

    v1.include_router(utterance.router)
    v1.include_router(media_router)
    v1.include_router(timer_routes.router)
    v1.include_router(idle.router)
    v1.include_router(diagnostics.router)
    v1.include_router(location.router)
    app.include_router(v1)
    # WebSocket routes authorize themselves (auth.authorize_websocket), so they live outside
    # the bearer-token dependency.
    app.include_router(events.router, prefix="/v1")
    # The pairing code is the credential here (the Display has no token yet): the only HTTP
    # route under /v1 without the bearer dependency (tests/test_pairing.py guards this).
    app.include_router(pair.router, prefix="/v1")
    # Spotify login: a browser on the Hub's Mac can't send the bearer header, so these
    # routes sit outside /v1 and only answer loopback clients (see media/spotify/auth.py).
    app.include_router(spotify_auth.router)
    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory: ``uvicorn coco_hub.app:create_app_from_env --factory``.

    Configures logging here (not in ``create_app``, which tests call many times) so the
    worker process logs JSON lines even when ``--reload`` runs it in a subprocess.
    """
    settings = load_settings()
    configure_logging(settings)
    return create_app(settings)
