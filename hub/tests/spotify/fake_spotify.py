"""An in-process stand-in for accounts.spotify.com and api.spotify.com (httpx.MockTransport).

Responses come from recorded-style JSON in ``tests/fixtures/spotify/``. Each route has a queue
of responses (consumed first) and a default; every request is recorded.
"""

import json
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "spotify"

IPAD_ID = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
TOKEN_PATH = "/api/token"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def json_response(status: int, name: str | None = None, **kwargs) -> httpx.Response:
    if name is None:
        return httpx.Response(status, **kwargs)
    return httpx.Response(status, json=fixture(name), **kwargs)


class FakeSpotify:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.queued: dict[tuple[str, str], deque[httpx.Response]] = defaultdict(deque)
        self.defaults: dict[tuple[str, str], httpx.Response] = {
            ("POST", TOKEN_PATH): json_response(200, "token_refreshed"),
            ("GET", "/v1/me/player/devices"): json_response(200, "devices"),
            ("GET", "/v1/me/player/currently-playing"): json_response(204),
            ("GET", "/v1/search"): json_response(200, "search_raffi"),
            ("PUT", "/v1/me/player/play"): json_response(204),
            ("PUT", "/v1/me/player/pause"): json_response(204),
            ("POST", "/v1/me/player/next"): json_response(204),
            ("PUT", "/v1/me/player/volume"): json_response(204),
        }
        self.transport = httpx.MockTransport(self)

    def queue(self, method: str, path: str, *responses: httpx.Response) -> None:
        self.queued[(method, path)].extend(responses)

    def set(self, method: str, path: str, response: httpx.Response) -> None:
        self.defaults[(method, path)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if self.queued[key]:
            return self.queued[key].popleft()
        if key in self.defaults:
            return self.defaults[key]
        return httpx.Response(404, json={"error": {"status": 404, "message": "no route"}})

    # --- inspection ------------------------------------------------------------------

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def api_calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests if r.url.path != TOKEN_PATH]

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    @staticmethod
    def body(request: httpx.Request) -> dict | None:
        return json.loads(request.content) if request.content else None


def spotify_settings(settings, **spotify):
    """``settings`` with a Spotify client id (and any other ``spotify.*`` overrides)."""
    values = {"client_id": "client-123", "device_name": "Kitchen iPad", **spotify}
    return settings.model_copy(
        update={"spotify": settings.spotify.model_copy(update=values)}, deep=True
    )


def log_in(conn, now: datetime) -> None:
    """Store a refresh token as if the owner had run the login flow."""
    from coco_hub.media.spotify.auth import TokenStore

    TokenStore(conn).save_login(
        refresh_token="AQRefreshTokenOne",
        access_token="BQAccessTokenFromCodeExchange",
        expires_at=now + timedelta(hours=1),
        scope="user-read-playback-state",
    )
