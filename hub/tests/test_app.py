import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coco_hub import discovery, identity
from coco_hub.app import create_app
from coco_hub.auth import authorize_websocket
from coco_hub.settings import Tls

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_healthz_is_open_and_names_the_hub(client, settings):
    response = client.get("/healthz")
    assert response.status_code == 200
    ident = identity.load_or_create(settings.data_dir)
    assert response.json() == {
        "status": "ok",
        "hub_id": ident.hub_id,
        "fingerprint": ident.short_fingerprint,
    }


def test_healthz_has_no_fingerprint_without_tls(settings):
    plain = settings.model_copy(update={"tls": Tls(enabled=False)})
    with TestClient(create_app(plain)) as c:
        body = c.get("/healthz").json()
    assert body["fingerprint"] is None
    assert body["hub_id"] == identity.load_or_create(settings.data_dir).hub_id


def test_ping_with_token(client):
    response = client.get("/v1/ping", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"pong": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Basic {TEST_TOKEN}"},
        {"Authorization": TEST_TOKEN},
        {"Authorization": "Bearer "},
    ],
)
def test_ping_rejects_bad_auth(client, headers):
    response = client.get("/v1/ping", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "Missing or invalid token"}
    }


def test_unknown_route_uses_error_format(client):
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_lifespan_opens_db_in_data_dir(settings):
    app = create_app(settings)
    with TestClient(app):
        assert app.state.db.execute("SELECT count(*) FROM meta").fetchone() == (0,)
        assert (settings.data_dir / "coco-hub.sqlite3").exists()


def test_media_services_registered(settings):
    from coco_hub.media.policy import AllowAllPolicy
    from coco_hub.media.youtube import YoutubeService

    app = create_app(settings)
    assert isinstance(app.state.services["media_policy"], AllowAllPolicy)
    with TestClient(app):
        youtube = app.state.services["youtube"]
        assert isinstance(youtube, YoutubeService)
        assert not youtube.configured  # no keys.youtube in tests
    assert "youtube" not in app.state.services


def ws_app(settings):
    app = create_app(settings)

    @app.websocket("/test-ws")
    async def endpoint(websocket: WebSocket) -> None:
        if not await authorize_websocket(websocket):
            return
        await websocket.accept()
        await websocket.send_json({"hello": True})
        await websocket.close()

    return app


def test_ws_accepts_bearer_header(settings):
    with TestClient(ws_app(settings)) as c:
        with c.websocket_connect("/test-ws", headers=AUTH) as ws:
            assert ws.receive_json() == {"hello": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer "},
        {"Authorization": TEST_TOKEN},
        {"Authorization": f"Basic {TEST_TOKEN}"},
    ],
)
def test_ws_rejects_missing_or_bad_header(settings, headers):
    with TestClient(ws_app(settings)) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/test-ws", headers=headers):
                pass
        assert exc.value.code == 1008


def test_ws_rejects_query_token_without_header(settings):
    """The token must never travel in the URL (it ends up in access logs)."""
    with TestClient(ws_app(settings)) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect(f"/test-ws?token={TEST_TOKEN}"):
                pass
        assert exc.value.code == 1008


class FakeAdvertiser:
    instances: list["FakeAdvertiser"] = []

    def __init__(self, settings, ident):
        self.started = False
        self.stopped = False
        FakeAdvertiser.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_advertiser(monkeypatch):
    FakeAdvertiser.instances = []
    monkeypatch.setattr(discovery, "Advertiser", FakeAdvertiser)
    return FakeAdvertiser


def test_discovery_not_started_when_disabled(settings, fake_advertiser):
    with TestClient(create_app(settings)):
        pass
    assert fake_advertiser.instances == []


def test_discovery_started_and_stopped_when_enabled(settings, fake_advertiser):
    discovery_on = settings.discovery.model_copy(update={"enabled": True})
    enabled = settings.model_copy(update={"discovery": discovery_on})
    with TestClient(create_app(enabled)):
        (adv,) = fake_advertiser.instances
        assert adv.started and not adv.stopped
    assert adv.stopped
