"""``POST /v1/utterance``: transcript in, ``Action`` out."""

from fastapi import APIRouter, Request

from coco_hub.intents.engine import IntentContext, NoMatch, RulesEngine
from coco_hub.protocol import Action, UtteranceRequest

router = APIRouter()


@router.post("/utterance", response_model=Action)
async def utterance(body: UtteranceRequest, request: Request) -> Action:
    state = request.app.state
    ctx = IntentContext(settings=state.settings, clock=state.clock, services=state.services)
    engine: RulesEngine = state.engine
    result = await engine.handle(body, ctx)
    if isinstance(result, NoMatch):
        return await state.fallback(result, body, ctx)
    return result
