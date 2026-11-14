"""Coco Hub FastAPI app."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from coco_hub import db, discovery
from coco_hub.auth import require_token
from coco_hub.errors import install_error_handlers
from coco_hub.settings import Settings, load_settings


def create_app(settings: Settings) -> FastAPI:
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

    app.include_router(v1)
    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory: ``uvicorn coco_hub.app:create_app_from_env --factory``."""
    return create_app(load_settings())
