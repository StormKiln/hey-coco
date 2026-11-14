"""``POST /v1/utterance``: transcript in, ``Action`` out. Every call is traced (diagnostics)."""

import time

from fastapi import APIRouter, Request

from coco_hub.diagnostics import DiagnosticsService
from coco_hub.intents.engine import IntentContext, NoMatch, RulesEngine
from coco_hub.protocol import Action, UtteranceRequest, UtteranceTrace

router = APIRouter()


@router.post("/utterance", response_model=Action)
async def utterance(body: UtteranceRequest, request: Request) -> Action:
    state = request.app.state
    ts = state.clock()
    started = time.perf_counter()
    action: Action | None = None
    error: str | None = None
    try:
        ctx = IntentContext(settings=state.settings, clock=state.clock, services=state.services)
        engine: RulesEngine = state.engine
        result = await engine.handle(body, ctx)
        action = await state.fallback(result, body, ctx) if isinstance(result, NoMatch) else result
        return action
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        diagnostics: DiagnosticsService = state.diagnostics
        diagnostics.record(
            UtteranceTrace(
                ts=ts,
                text=body.text,
                source=body.source,
                view=body.context.view,
                intent=action.intent if action else None,
                matched_by=action.matched_by if action else None,
                say=action.say if action else None,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=error,
            )
        )
