"""Coco Hub FastAPI app."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, FastAPI

from coco_hub import db, discovery
from coco_hub.auth import require_token
from coco_hub.errors import install_error_handlers
from coco_hub.intents import build_registry
from coco_hub.intents.engine import RulesEngine, no_fallback
from coco_hub.routes import utterance
from coco_hub.settings import Settings, load_settings
from coco_hub.sounds import SoundLibrary, media_router


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(settings: Settings, *, clock: Callable[[], datetime] = _utc_now) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db.open_db(settings.data_dir)
        advertiser = None
        try:
            if settings.discovery.enabled:
                advertiser = discovery.Advertiser(settings)
                await advertiser.start()
            yield
        finally:
            if advertiser is not None:
                await advertiser.stop()
            app.state.db.close()

    app = FastAPI(title="Coco Hub", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    # Intent pipeline. Feature modules register intents into `app.state.intents` (see
    # coco_hub.intents.INTENT_MODULES), put their services in `app.state.services` (handed to
    # handlers as `ctx.services`), and the Qwen fallback replaces `app.state.fallback`.
    app.state.intents = build_registry()
    app.state.engine = RulesEngine(app.state.intents)
    app.state.services = {}
    app.state.fallback = no_fallback
    app.state.sounds = SoundLibrary.load(settings.sounds_dir)
    app.state.services["sounds"] = app.state.sounds
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
    app.include_router(v1)
    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory: ``uvicorn coco_hub.app:create_app_from_env --factory``."""
    return create_app(load_settings())
