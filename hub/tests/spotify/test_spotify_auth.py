import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from coco_hub import db
from coco_hub.media.spotify.auth import (
    SCOPES,
    LoginError,
    SpotifyAuth,
    TokenStore,
    code_challenge,
    make_verifier,
)
from coco_hub.media.spotify.errors import (
    SpotifyError,
    SpotifyNotConfigured,
    SpotifyNotConnected,
)

from fake_spotify import TOKEN_PATH, FakeSpotify, json_response

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
REDIRECT = "http://127.0.0.1:8765/spotify/callback"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def auth(conn, clock, fake) -> SpotifyAuth:
    return SpotifyAuth(
        conn, client_id="client-123", redirect_uri=REDIRECT, clock=clock, transport=fake.transport
    )


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def _login(auth: SpotifyAuth, fake: FakeSpotify) -> None:
    fake.queue("POST", TOKEN_PATH, json_response(200, "token"))
    state = _params(auth.login_url())["state"]
    await auth.complete_login("the-code", state)


# --- PKCE --------------------------------------------------------------------------------


def test_verifier_is_rfc7636_shaped():
    verifier = make_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    assert make_verifier() != verifier


def test_challenge_is_s256_base64url_without_padding():
    # RFC 7636 appendix B.
    verifier = "dBjftJeZ4CVP-mJ92K9b5Wz6Z5zK5j-0pz1bc0Ezlhz1HC"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert code_challenge(verifier) == expected.decode()
    assert "=" not in code_challenge(verifier)


def test_login_url_asks_for_the_playback_scopes(auth):
    url = auth.login_url()
    assert url.startswith("https://accounts.spotify.com/authorize?")
    params = _params(url)
    assert params["client_id"] == "client-123"
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == REDIRECT
    assert params["code_challenge_method"] == "S256"
    assert (
        set(params["scope"].split())
        == set(SCOPES)
        == {
            "user-read-playback-state",
            "user-modify-playback-state",
            "user-read-currently-playing",
        }
    )
    assert len(params["state"]) >= 16


async def test_complete_login_exchanges_code_with_the_verifier(auth, fake, conn):
    fake.queue("POST", TOKEN_PATH, json_response(200, "token"))
    params = _params(auth.login_url())
    assert not auth.logged_in
    await auth.complete_login("the-code", params["state"])

    (request,) = fake.calls("POST", TOKEN_PATH)
    form = fake.form(request)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == REDIRECT
    assert form["client_id"] == "client-123"
    assert code_challenge(form["code_verifier"]) == params["code_challenge"]
    assert "client_secret" not in form  # PKCE: no secret leaves the Hub

    assert auth.logged_in
    stored = TokenStore(conn).load()
    assert stored is not None
    assert stored.refresh_token == "AQRefreshTokenOne"
    assert stored.access_token == "BQAccessTokenFromCodeExchange"
    assert stored.expires_at == T0 + timedelta(seconds=3600)


async def test_unknown_state_is_rejected(auth, fake):
    auth.login_url()
    with pytest.raises(LoginError):
        await auth.complete_login("the-code", "forged-state")
    assert fake.requests == []
    assert not auth.logged_in


async def test_state_is_single_use(auth, fake):
    fake.queue("POST", TOKEN_PATH, json_response(200, "token"))
    state = _params(auth.login_url())["state"]
    await auth.complete_login("the-code", state)
    with pytest.raises(LoginError):
        await auth.complete_login("the-code", state)


async def test_stale_state_is_rejected(auth, fake, clock):
    state = _params(auth.login_url())["state"]
    clock.now = T0 + timedelta(minutes=11)
    with pytest.raises(LoginError):
        await auth.complete_login("the-code", state)
    assert fake.requests == []


async def test_failed_code_exchange_is_a_login_error(auth, fake):
    fake.queue("POST", TOKEN_PATH, json_response(400, "token_invalid_grant"))
    state = _params(auth.login_url())["state"]
    with pytest.raises(LoginError):
        await auth.complete_login("bad-code", state)
    assert not auth.logged_in


# --- access tokens -----------------------------------------------------------------------


async def test_unconfigured_and_not_connected(conn, clock, fake, auth):
    unconfigured = SpotifyAuth(conn, client_id=None, redirect_uri=REDIRECT, clock=clock)
    assert not unconfigured.configured
    with pytest.raises(SpotifyNotConfigured):
        await unconfigured.access_token()
    with pytest.raises(SpotifyNotConnected):
        await auth.access_token()


async def test_access_token_is_reused_until_near_expiry(auth, fake, clock):
    await _login(auth, fake)
    assert await auth.access_token() == "BQAccessTokenFromCodeExchange"
    clock.now = T0 + timedelta(minutes=58, seconds=59)
    assert await auth.access_token() == "BQAccessTokenFromCodeExchange"
    assert len(fake.calls("POST", TOKEN_PATH)) == 1

    clock.now = T0 + timedelta(minutes=59, seconds=30)
    assert await auth.access_token() == "BQAccessTokenRefreshed"
    refresh = fake.calls("POST", TOKEN_PATH)[-1]
    assert fake.form(refresh) == {
        "grant_type": "refresh_token",
        "refresh_token": "AQRefreshTokenOne",
        "client_id": "client-123",
    }


async def test_refresh_keeps_the_old_refresh_token_unless_rotated(auth, fake, conn):
    await _login(auth, fake)
    await auth.refresh()
    assert TokenStore(conn).load().refresh_token == "AQRefreshTokenOne"
    fake.queue(
        "POST",
        TOKEN_PATH,
        json_response(
            200,
            json={"access_token": "BQNew", "expires_in": 3600, "refresh_token": "AQRotated"},
        ),
    )
    assert await auth.refresh() == "BQNew"
    assert TokenStore(conn).load().refresh_token == "AQRotated"


async def test_revoked_refresh_token_logs_out(auth, fake):
    await _login(auth, fake)
    fake.queue("POST", TOKEN_PATH, json_response(400, "token_invalid_grant"))
    with pytest.raises(SpotifyNotConnected):
        await auth.refresh()
    assert not auth.logged_in


async def test_refresh_server_error_keeps_the_login(auth, fake):
    await _login(auth, fake)
    fake.queue("POST", TOKEN_PATH, json_response(500, json={"error": "server_error"}))
    with pytest.raises(SpotifyError):
        await auth.refresh()
    assert auth.logged_in


def test_tokens_survive_a_restart(conn, clock, fake, auth):
    TokenStore(conn).save_login(
        refresh_token="AQStored", access_token="BQStored", expires_at=T0, scope="x"
    )
    again = SpotifyAuth(conn, client_id="client-123", redirect_uri=REDIRECT, clock=clock)
    assert again.logged_in
