"""Shared LAN token auth.

HTTP: ``Authorization: Bearer <token>`` on everything except ``GET /healthz``.
WebSocket: ``?token=<token>`` query parameter.
"""

import hmac

from fastapi import Request, WebSocket, status

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


async def require_token(request: Request) -> None:
    """FastAPI dependency: reject the request unless it carries the bearer token."""
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token_matches(
        request.app.state.settings, credentials.strip()
    ):
        raise _unauthorized()


async def authorize_websocket(websocket: WebSocket) -> bool:
    """Check ``?token=``; on failure close with 1008 and return False.

    Call before ``websocket.accept()``.
    """
    if token_matches(websocket.app.state.settings, websocket.query_params.get("token")):
        return True
    await websocket.close(code=WS_POLICY_VIOLATION, reason="unauthorized")
    return False
