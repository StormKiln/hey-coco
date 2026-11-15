"""Timer REST endpoints (touch UI). Voice goes through ``/v1/utterance`` intents.

Handlers are ``async`` so they run on the event loop thread, like the service's event bus.
"""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Request, status

from coco_hub.errors import HubError
from coco_hub.protocol import Timer, TimerAddRequest, TimerCreateRequest, TimersList
from coco_hub.timers.service import TimerLimitError, TimerNotFound, TimerService, TimerStateError

router = APIRouter(prefix="/timers")


def _service(request: Request) -> TimerService:
    return request.app.state.services["timers"]


def _call(fn: Callable[[str], Timer], timer_id: str) -> Timer:
    try:
        return fn(timer_id)
    except TimerNotFound as exc:
        raise HubError(status.HTTP_404_NOT_FOUND, "not_found", f"No timer {timer_id!r}") from exc
    except TimerStateError as exc:
        raise HubError(status.HTTP_409_CONFLICT, "conflict", str(exc)) from exc


@router.get("", response_model=TimersList)
async def list_timers(request: Request) -> TimersList:
    clock: Callable[[], datetime] = request.app.state.clock
    return TimersList(timers=_service(request).list_active(), server_time=clock())


@router.post("", response_model=Timer)
async def create_timer(body: TimerCreateRequest, request: Request) -> Timer:
    try:
        return _service(request).create(body.name, body.duration_s)
    except TimerLimitError as exc:
        raise HubError(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_timer", str(exc)) from exc


@router.post("/{timer_id}/dismiss", response_model=Timer)
async def dismiss_timer(timer_id: str, request: Request) -> Timer:
    return _call(_service(request).dismiss, timer_id)


@router.post("/{timer_id}/add", response_model=Timer)
async def add_time(timer_id: str, body: TimerAddRequest, request: Request) -> Timer:
    """Same rules as the voice "add N minutes" (``TimerService.add_time``)."""
    try:
        return _call(lambda tid: _service(request).add_time(tid, body.seconds), timer_id)
    except TimerLimitError as exc:
        raise HubError(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_timer", str(exc)) from exc


@router.delete("/{timer_id}", response_model=Timer)
async def cancel_timer(timer_id: str, request: Request) -> Timer:
    return _call(_service(request).cancel, timer_id)
