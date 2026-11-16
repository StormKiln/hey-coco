"""``POST /v1/pair``: trade the one-time code from ``make hub-pair`` for the shared token (#89).

The one /v1 route without the bearer token (the Display doesn't have it yet). The Display only
sends the code over TLS pinned to the certificate the person confirmed. Never logs the code or
the token.
"""

import logging

from fastapi import APIRouter, Request, status

from coco_hub import pairing
from coco_hub.errors import HubError
from coco_hub.protocol import PairRequest, PairResponse

router = APIRouter()
log = logging.getLogger(__name__)

_ERRORS = {
    pairing.Redeem.NO_CODE: (
        status.HTTP_404_NOT_FOUND,
        "no_pairing_code",
        "No pairing code is active. Run make hub-pair on the Mac.",
    ),
    pairing.Redeem.WRONG: (
        status.HTTP_401_UNAUTHORIZED,
        "pairing_code_wrong",
        "That code is wrong. Check it and try again.",
    ),
    pairing.Redeem.EXPIRED: (
        status.HTTP_410_GONE,
        "pairing_code_expired",
        "That code has expired. Run make hub-pair on the Mac for a new one.",
    ),
    pairing.Redeem.LOCKED: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "pairing_code_locked",
        "Too many wrong codes. Run make hub-pair on the Mac for a new one.",
    ),
}


@router.post("/pair", response_model=PairResponse)
async def pair(body: PairRequest, request: Request) -> PairResponse:
    state = request.app.state
    result = pairing.redeem_code(state.db, body.code, now=state.clock())
    if result is pairing.Redeem.OK:
        log.info("A Display paired with a pairing code")
        return PairResponse(token=state.settings.token.get_secret_value())
    log.warning("Pairing code refused: %s", result.value)
    status_code, code, message = _ERRORS[result]
    raise HubError(status_code, code, message)
