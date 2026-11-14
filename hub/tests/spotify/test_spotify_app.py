from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from coco_hub import db
from coco_hub.app import create_app
from coco_hub.media.spotify.auth import TokenStore
from coco_hub.media.spotify.client import parse_currently_playing
from coco_hub.protocol import EVENT_ADAPTER

from conftest import TEST_TOKEN
from fake_spotify import TOKEN_PATH, FakeSpotify, fixture, json_response, spotify_settings

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
LOOPBACK = ("127.0.0.1", 50123)


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def configured(settings):
    return spotify_settings(settings)


def _client(settings, fake, client=LOOPBACK) -> TestClient:
    app = create_app(settings, clock=lambda: T0, spotify_transport=fake.transport)
    return TestClient(app, client=client, follow_redirects=False)


@pytest.mark.parametrize("path", ["/spotify/login", "/spotify/callback?code=x&state=y"])
@pytest.mark.parametrize("host", ["192.168.1.40", "10.0.0.7", "testclient"])
def test_login_routes_are_loopback_only(configured, fake, path, host):
    with _client(configured, fake, client=(host, 50000)) as client:
        # Not even the LAN bearer token gets you in.
        response = client.get(path, headers=AUTH)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert fake.requests == []


def test_login_redirects_to_spotify_and_callback_stores_tokens(configured, fake):
    fake.queue("POST", TOKEN_PATH, json_response(200, "token"))
    with _client(configured, fake) as client:
        login = client.get("/spotify/login")
        assert login.status_code in (302, 307)
        location = login.headers["location"]
        assert location.startswith("https://accounts.spotify.com/authorize?")
        state = parse_qs(urlsplit(location).query)["state"][0]

        done = client.get(f"/spotify/callback?code=the-code&state={state}")
        assert done.status_code == 200
        assert "connected" in done.text.lower()
        conn = client.app.state.db
        assert TokenStore(conn).load().refresh_token == "AQRefreshTokenOne"
        assert client.app.state.services["spotify"].logged_in


def test_ipv6_loopback_is_allowed(configured, fake):
    with _client(configured, fake, client=("::1", 50000)) as client:
        assert client.get("/spotify/login").status_code in (302, 307)


def test_callback_with_bad_state_or_denied_consent(configured, fake):
    with _client(configured, fake) as client:
        client.get("/spotify/login")
        bad = client.get("/spotify/callback?code=x&state=forged")
        assert bad.status_code == 400
        denied = client.get("/spotify/callback?error=access_denied&state=whatever")
        assert denied.status_code == 400
        assert "access_denied" in denied.text
        assert not client.app.state.services["spotify"].logged_in
    assert fake.requests == []


def test_login_when_unconfigured(settings, fake):
    with _client(settings, fake) as client:
        response = client.get("/spotify/login")
    assert response.status_code == 409
    assert "client_id" in response.json()["error"]["message"]


def test_login_routes_are_not_under_v1(configured, fake):
    with _client(configured, fake) as client:
        assert client.get("/v1/spotify/login", headers=AUTH).status_code == 404


def test_unconfigured_hub_never_calls_spotify(settings, fake):
    with _client(settings, fake) as client:
        with client.websocket_connect("/v1/events", headers=AUTH) as ws:
            hello = EVENT_ADAPTER.validate_python(ws.receive_json())
        assert hello.now_playing is None
        response = client.post(
            "/v1/utterance",
            json={
                "text": "play Raffi",
                "source": "voice",
                "context": {"view": "idle", "alarm_ringing": False, "media_playing": None},
            },
            headers=AUTH,
        )
        assert response.json()["say"] == "Spotify isn't set up yet."
    assert fake.requests == []


def test_hello_includes_now_playing(configured, fake, tmp_path):
    with _client(configured, fake) as client:
        spotify = client.app.state.services["spotify"]
        spotify._now_playing = parse_currently_playing(fixture("currently_playing"))
        with client.websocket_connect("/v1/events", headers=AUTH) as ws:
            hello = EVENT_ADAPTER.validate_python(ws.receive_json())
    assert hello.type == "hello"
    assert hello.now_playing is not None
    assert hello.now_playing.title == "Bananaphone"
    assert hello.now_playing.source == "spotify"


def test_migration_creates_the_token_table(tmp_path):
    conn = db.open_db(tmp_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "spotify_tokens" in tables
    finally:
        conn.close()
