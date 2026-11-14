"""Shared LAN token auth.

``Authorization: Bearer <token>`` on everything except ``GET /healthz``, including the
WebSocket upgrade request. The token is never accepted in the URL (query strings end up in
access logs).
"""

import hmac

from fastapi import Request, WebSocket, status
from starlette.datastructures import Headers

from coco_hub.errors import HubError
from coco_hub.settings import Settings

WS_POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


def token_matches(settings: Settings, candidate: str | None) -> bool:
    if not candidate:
        return False
    expected = settings.token.get_secret_value().encode()
    return hmac.compare_digest(expected, candidate.encode())


def _unauthorized() -> HubError:
    return HubError(
        status.HTTP_401_UNAUTHORIZED,
        "unauthorized",
        "Missing or invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_matches(settings: Settings, headers: Headers) -> bool:
    """True if ``headers`` carry ``Authorization: Bearer <token>`` with the shared token."""
    scheme, _, credentials = headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and token_matches(settings, credentials.strip())


async def require_token(request: Request) -> None:
    """FastAPI dependency: reject the request unless it carries the bearer token."""
    if not bearer_matches(request.app.state.settings, request.headers):
        raise _unauthorized()


async def authorize_websocket(websocket: WebSocket) -> bool:
    """Check the upgrade request's bearer header; on failure close with 1008 and return False.

    Call before ``websocket.accept()``.
    """
    if bearer_matches(websocket.app.state.settings, websocket.headers):
        return True
    await websocket.close(code=WS_POLICY_VIOLATION, reason="unauthorized")
    return False
