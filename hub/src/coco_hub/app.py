"""Coco Hub FastAPI app."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, FastAPI

from coco_hub import db, discovery, events
from coco_hub.auth import require_token
from coco_hub.errors import install_error_handlers
from coco_hub.intents import build_registry
from coco_hub.intents.engine import RulesEngine, no_fallback
from coco_hub.media.policy import AllowAllPolicy
from coco_hub.media.youtube import YoutubeService
from coco_hub.qwen.client import OllamaClient
from coco_hub.qwen.fallback import QwenFallback
from coco_hub.routes import idle, utterance
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
    ollama_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    weather = weather or WeatherService(settings, clock=clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db.open_db(settings.data_dir)
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
        # The first tick also settles timers that ended while the Hub was down.
        timer_task = asyncio.create_task(timers.run(), name="timers")
        advertiser = None
        weather_task = None
        qwen: QwenFallback | None = app.state.qwen
        try:
            if settings.discovery.enabled:
                advertiser = discovery.Advertiser(settings)
                await advertiser.start()
            if weather.configured:
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
            if advertiser is not None:
                await advertiser.stop()
            timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer_task
            app.state.services.pop("timers", None)
            await youtube.aclose()
            app.state.services.pop("youtube", None)
            app.state.db.close()

    app = FastAPI(title="Coco Hub", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    # Intent pipeline. Feature modules register intents into `app.state.intents` (see
    # coco_hub.intents.INTENT_MODULES), put their services in `app.state.services` (handed to
    # handlers as `ctx.services`), and the Qwen fallback replaces `app.state.fallback`.
    app.state.intents = build_registry()
    app.state.engine = RulesEngine(app.state.intents)
    app.state.services = {"weather": weather}
    app.state.fallback = no_fallback
    app.state.qwen = None
    if settings.qwen.enabled:
        app.state.qwen = QwenFallback(
            OllamaClient(settings.qwen.base_url, settings.qwen.model, transport=ollama_transport),
            app.state.engine,
            timeout_s=settings.qwen.timeout_s,
        )
        app.state.fallback = app.state.qwen
    app.state.sounds = SoundLibrary.load(settings.sounds_dir)
    app.state.services["sounds"] = app.state.sounds
    # The one media choke point (parental controls go here; see coco_hub.media.policy).
    app.state.services["media_policy"] = AllowAllPolicy()
    app.state.events = events.EventBus()
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
    app.include_router(v1)
    # WebSocket routes authorize themselves (auth.authorize_websocket), so they live outside
    # the bearer-token dependency.
    app.include_router(events.router, prefix="/v1")
    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory: ``uvicorn coco_hub.app:create_app_from_env --factory``."""
    return create_app(load_settings())
