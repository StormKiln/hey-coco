"""In-process event bus and the ``WS /v1/events`` stream.

Services publish protocol ``Event`` models; every WebSocket client holds a ``Subscription``
with a bounded queue. Publishing never blocks: a subscriber whose queue is full is dropped
(its ``get()`` returns ``None``) and its socket is closed, so the Display reconnects and
resyncs from ``hello`` instead of slowing everyone else down.

Everything runs on the event loop thread: call ``publish`` from async code (or sync code
called from it), never from a worker thread.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, get_args

from fastapi import APIRouter, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from coco_hub.auth import authorize_websocket
from coco_hub.protocol import EVENT_ADAPTER, Event, HelloEvent

log = logging.getLogger(__name__)

DEFAULT_MAX_QUEUE = 100
_EVENT_TYPES = tuple(get_args(get_args(Event)[0]))


class Subscription:
    def __init__(self, bus: "EventBus", max_queue: int) -> None:
        self._bus = bus
        self._max_queue = max_queue
        self._queue: deque[Event] = deque()
        self._ready = asyncio.Event()  # binds to a loop lazily, on first wait
        self.dropped = False
        self.closed = False

    def _push(self, event: Event) -> bool:
        """Queue ``event``; returns False (and marks dropped) if the queue overflowed."""
        if len(self._queue) >= self._max_queue:
            self.dropped = True
            self._queue.clear()
            self._signal()
            return False
        self._queue.append(event)
        self._signal()
        return True

    def _signal(self) -> None:
        self._ready.set()

    def get_nowait(self) -> Event | None:
        """Next queued event, or None if there is none (or the subscriber was dropped)."""
        if self.dropped or not self._queue:
            return None
        return self._queue.popleft()

    async def get(self) -> Event | None:
        """Wait for the next event; None once dropped or unsubscribed."""
        while True:
            if self.dropped or self.closed:
                return None
            if self._queue:
                return self._queue.popleft()
            self._ready.clear()
            await self._ready.wait()

    def close(self) -> None:
        self.closed = True
        self._bus._remove(self)
        self._signal()


class EventBus:
    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE) -> None:
        self.max_queue = max_queue
        self._subscribers: list[Subscription] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[Subscription]:
        sub = Subscription(self, self.max_queue)
        self._subscribers.append(sub)
        try:
            yield sub
        finally:
            sub.close()

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    def publish(self, event: Event) -> None:
        if not isinstance(event, _EVENT_TYPES):
            raise TypeError(f"not a protocol event: {type(event).__name__}")
        for sub in list(self._subscribers):
            if not sub._push(event):
                log.warning("dropping slow event subscriber")
                self._remove(sub)

    @staticmethod
    def encode(event: Event) -> str:
        return EVENT_ADAPTER.dump_json(event).decode()


# --- WS /v1/events -----------------------------------------------------------------------

router = APIRouter()


def build_hello(state: Any) -> HelloEvent:
    clock: Callable[[], datetime] = state.clock
    timers = state.services["timers"].list_active()
    return HelloEvent(server_time=clock(), timers=timers, now_playing=None)


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Read (and ignore) client messages until the client goes away."""
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    if not await authorize_websocket(websocket):
        return
    await websocket.accept()
    state = websocket.app.state
    bus: EventBus = state.events
    # Subscribe before taking the snapshot so nothing between them is lost.
    with bus.subscribe() as sub:
        await websocket.send_text(EventBus.encode(build_hello(state)))
        disconnected = asyncio.create_task(_wait_for_disconnect(websocket))
        try:
            while True:
                getter = asyncio.create_task(sub.get())
                done, _ = await asyncio.wait(
                    {getter, disconnected}, return_when=asyncio.FIRST_COMPLETED
                )
                if disconnected in done:
                    getter.cancel()
                    return
                event = getter.result()
                if event is None:  # dropped as a slow consumer
                    await websocket.close(
                        code=status.WS_1013_TRY_AGAIN_LATER, reason="too slow; reconnect"
                    )
                    return
                await websocket.send_text(EventBus.encode(event))
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            disconnected.cancel()
