"""Spotify login: OAuth Authorization Code with PKCE (RFC 7636), tokens kept on the Hub.

The owner logs in once, in a browser on the Hub's own Mac::

    open http://127.0.0.1:8765/spotify/login     # or: make spotify-login

``GET /spotify/login`` redirects to Spotify's consent page; Spotify redirects back to
``GET /spotify/callback`` (the app's registered redirect URI), which exchanges the code for an
access token and a refresh token. Both routes are reachable **only from loopback**: they live
outside the bearer-token ``/v1`` router (a browser can't send the header) and answer 403 to
any other client address, so nobody on the LAN can link their own Spotify account or start a
login. The ``state`` parameter is single-use and expires after 10 minutes.

The refresh token is stored in the Hub's SQLite database in ``data_dir`` (never in git, never
sent to the Display). Access tokens are refreshed automatically a minute before they expire,
or when the API answers 401. A refresh token Spotify rejects (``invalid_grant``) is deleted,
and Coco says "Spotify needs to be connected on the Mac first."
"""

import asyncio
import base64
import hashlib
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from coco_hub.errors import HubError
from coco_hub.media.spotify.errors import SpotifyError, SpotifyNotConfigured, SpotifyNotConnected

if TYPE_CHECKING:
    from coco_hub.media.spotify.service import SpotifyService

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
)
PENDING_TTL = timedelta(minutes=10)
REFRESH_MARGIN = timedelta(seconds=60)
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"


def make_verifier() -> str:
    """A PKCE code verifier: 86 URL-safe characters (RFC 7636 allows 43-128)."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """The S256 challenge: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class LoginError(Exception):
    """The callback can't complete a login (bad or stale state, failed exchange)."""


# --- storage -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredTokens:
    refresh_token: str
    access_token: str | None
    expires_at: datetime | None
    scope: str | None


class TokenStore:
    """The single row of ``spotify_tokens``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self) -> StoredTokens | None:
        row = self._conn.execute(
            "SELECT refresh_token, access_token, expires_at, scope FROM spotify_tokens WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        expires = datetime.fromisoformat(row[2]) if row[2] else None
        return StoredTokens(
            refresh_token=row[0], access_token=row[1], expires_at=expires, scope=row[3]
        )

    def save_login(
        self,
        *,
        refresh_token: str,
        access_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO spotify_tokens "
                "(id, refresh_token, access_token, expires_at, scope, updated_at) "
                "VALUES (1, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
                "ON CONFLICT (id) DO UPDATE SET refresh_token = excluded.refresh_token, "
                "access_token = excluded.access_token, expires_at = excluded.expires_at, "
                "scope = excluded.scope, updated_at = excluded.updated_at",
                (
                    refresh_token,
                    access_token,
                    expires_at.isoformat() if expires_at else None,
                    scope,
                ),
            )

    def clear(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM spotify_tokens")


# --- auth --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pending:
    verifier: str
    created_at: datetime


class SpotifyAuth:
    """Builds the login URL, completes the callback and hands out fresh access tokens."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        client_id: str | None,
        redirect_uri: str,
        clock: Callable[[], datetime],
        transport: httpx.AsyncBaseTransport | None = None,
        token_url: str = TOKEN_URL,
        authorize_url: str = AUTHORIZE_URL,
    ) -> None:
        self.client_id = client_id or None
        self.redirect_uri = redirect_uri
        self._store = TokenStore(conn)
        self._clock = clock
        self._transport = transport
        self._token_url = token_url
        self._authorize_url = authorize_url
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return self.client_id is not None

    @property
    def logged_in(self) -> bool:
        return self.configured and self._store.load() is not None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    # --- login -------------------------------------------------------------------------

    def login_url(self) -> str:
        if self.client_id is None:
            raise SpotifyNotConfigured()
        now = self._clock()
        self._pending = {s: p for s, p in self._pending.items() if now - p.created_at < PENDING_TTL}
        state = secrets.token_urlsafe(24)
        verifier = make_verifier()
        self._pending[state] = _Pending(verifier=verifier, created_at=now)
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge(verifier),
                "scope": " ".join(SCOPES),
                "state": state,
            }
        )
        return f"{self._authorize_url}?{query}"

    async def complete_login(self, code: str, state: str) -> None:
        pending = self._pending.pop(state, None)
        if pending is None or self._clock() - pending.created_at >= PENDING_TTL:
            raise LoginError("unknown or expired login; start again")
        if self.client_id is None:
            raise LoginError("Spotify isn't configured")
        try:
            data = await self._token_request(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "code_verifier": pending.verifier,
                }
            )
        except SpotifyError as exc:
            raise LoginError(str(exc)) from exc
        refresh_token = data.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise LoginError("Spotify sent no refresh token")
        self._save(data, refresh_token)

    # --- tokens ------------------------------------------------------------------------

    async def access_token(self) -> str:
        """A valid access token, refreshed when it is about to expire."""
        tokens = self._current()
        if (
            tokens.access_token
            and tokens.expires_at is not None
            and tokens.expires_at - REFRESH_MARGIN > self._clock()
        ):
            return tokens.access_token
        return await self.refresh(stale=tokens.access_token)

    async def refresh(self, stale: str | None = None) -> str:
        """Get a new access token. With ``stale``, skip the refresh if another caller has
        already replaced that token (concurrent 401s refresh once)."""
        async with self._lock:
            tokens = self._current()
            if (
                stale is not None
                and tokens.access_token
                and tokens.access_token != stale
                and tokens.expires_at is not None
                and tokens.expires_at - REFRESH_MARGIN > self._clock()
            ):
                return tokens.access_token
            try:
                data = await self._token_request(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": tokens.refresh_token,
                        "client_id": self.client_id or "",
                    }
                )
            except _TokenRejected:
                self._store.clear()
                raise SpotifyNotConnected("Spotify rejected the refresh token") from None
            # Spotify may rotate the refresh token; keep the old one when it doesn't.
            return self._save(data, data.get("refresh_token") or tokens.refresh_token)

    def _current(self) -> StoredTokens:
        if self.client_id is None:
            raise SpotifyNotConfigured()
        tokens = self._store.load()
        if tokens is None:
            raise SpotifyNotConnected()
        return tokens

    def _save(self, data: dict[str, Any], refresh_token: str) -> str:
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise SpotifyError("Spotify sent no access token")
        expires_in = data.get("expires_in")
        seconds = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
        self._store.save_login(
            refresh_token=refresh_token,
            access_token=access,
            expires_at=self._clock() + timedelta(seconds=seconds),
            scope=data.get("scope") if isinstance(data.get("scope"), str) else None,
        )
        return access

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._client().post(self._token_url, data=form)
        except httpx.HTTPError as exc:
            raise SpotifyError(f"Spotify token request failed: {type(exc).__name__}") from None
        if response.status_code == 400:
            # invalid_grant / invalid_client: the code or refresh token is no good.
            raise _TokenRejected(f"Spotify rejected the token request: {_oauth_error(response)}")
        if response.status_code != 200:
            raise SpotifyError(f"Spotify token endpoint answered HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SpotifyError("Spotify sent an unreadable token response") from exc
        if not isinstance(data, dict):
            raise SpotifyError("Spotify sent an unreadable token response")
        return data

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


class _TokenRejected(SpotifyError):
    pass


def _oauth_error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", "unknown"))
    except (ValueError, AttributeError):
        return "unknown"


# --- routes ------------------------------------------------------------------------------

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

router = APIRouter(prefix="/spotify", include_in_schema=False)


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in LOOPBACK_HOSTS:
        raise HubError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Spotify login only works from the Hub's own Mac: open "
            "http://127.0.0.1:<port>/spotify/login there.",
        )


def _service(request: Request) -> "SpotifyService":
    return request.app.state.services["spotify"]


def _page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    from html import escape

    body = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Coco Hub - Spotify</title>"
        "<style>body{font:18px system-ui;margin:4em auto;max-width:32em;padding:0 1em}"
        "</style></head>"
        f"<body><h1>{escape(title)}</h1><p>{escape(message)}</p></body></html>"
    )
    return HTMLResponse(body, status_code=status_code)


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    _require_loopback(request)
    service = _service(request)
    if not service.configured:
        raise HubError(
            status.HTTP_409_CONFLICT,
            "conflict",
            "Spotify isn't set up: add spotify.client_id to hub/config.local.toml "
            "(see docs/spotify.md) and restart the Hub.",
        )
    return RedirectResponse(service.auth.login_url(), status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    _require_loopback(request)
    if error is not None:
        return _page("Spotify not connected", f"Spotify said: {error}. Try again.", 400)
    if not code or not state:
        return _page("Spotify not connected", "The callback is missing its code.", 400)
    try:
        await _service(request).complete_login(code, state)
    except LoginError as exc:
        return _page("Spotify not connected", f"{exc}. Open /spotify/login again.", 400)
    return _page("Spotify is connected", "Coco can play music now. You can close this tab.")
