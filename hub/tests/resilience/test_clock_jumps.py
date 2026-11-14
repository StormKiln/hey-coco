"""Wall-clock jumps (Mac sleep/wake, NTP corrections) seen by the timer runner.

Unit level with an injectable clock (a subprocess can't move the system clock), so these run
in the default ``make hub-test`` suite, not under the ``resilience`` marker. Timers store
absolute UTC end times; the runner re-checks at least every ``MAX_SLEEP_S`` (patched short
here), so a jump is noticed without anyone calling ``wake()``.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coco_hub import db
from coco_hub.events import EventBus
from coco_hub.timers import service as timer_service
from coco_hub.timers.service import TimerService

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)


class JumpyClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def jump(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> JumpyClock:
    return JumpyClock()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def service(tmp_path, clock, bus, monkeypatch):
    monkeypatch.setattr(timer_service, "MAX_SLEEP_S", 0.02)
    conn = db.open_db(tmp_path)
    yield TimerService(conn, clock=clock, bus=bus, ring_cap_s=600, missed_grace_s=3600)
    conn.close()


async def next_event(sub, timeout: float = 1.0):
    return await asyncio.wait_for(sub.get(), timeout)


async def running(service: TimerService):
    task = asyncio.create_task(service.run())
    await asyncio.sleep(0.05)  # let it tick and go to sleep
    return task


async def test_forward_jump_past_end_fires_without_a_wake(service, clock, bus):
    timer = service.create("pasta", 600)
    task = await running(service)
    try:
        with bus.subscribe() as sub:
            clock.jump(minutes=20)  # the Mac slept through the end
            event = await next_event(sub)
    finally:
        task.cancel()
    assert (event.type, event.timer.id) == ("timer.fired", timer.id)
    assert service.get(timer.id).state == "ringing"


async def test_forward_jump_past_grace_dismisses_silently(service, clock, bus):
    timer = service.create("pasta", 600)
    task = await running(service)
    try:
        with bus.subscribe() as sub:
            clock.jump(hours=3)  # slept for hours: a stale alarm helps nobody
            event = await next_event(sub)
    finally:
        task.cancel()
    assert (event.type, event.timer.id) == ("timer.dismissed", timer.id)
    assert service.list_active() == []


async def test_forward_jump_past_ring_cap_dismisses_a_ringing_timer(service, clock, bus):
    timer = service.create("pasta", 60)
    clock.jump(seconds=60)
    service.tick()
    assert service.get(timer.id).state == "ringing"
    task = await running(service)
    try:
        with bus.subscribe() as sub:
            clock.jump(minutes=11)
            event = await next_event(sub)
    finally:
        task.cancel()
    assert (event.type, event.timer.id) == ("timer.dismissed", timer.id)


async def test_backward_jump_neither_fires_early_nor_loses_the_timer(service, clock, bus):
    timer = service.create("pasta", 600)
    task = await running(service)
    try:
        with bus.subscribe() as sub:
            clock.jump(minutes=-30)  # NTP pulled the clock back
            await asyncio.sleep(0.1)  # several ticks
            assert sub.get_nowait() is None
            assert service.get(timer.id) == timer  # same absolute end, still running

            clock.jump(minutes=30, seconds=600)  # back to real time, at the end
            event = await next_event(sub)
    finally:
        task.cancel()
    assert (event.type, event.timer.id) == ("timer.fired", timer.id)
    assert not task.done() or task.cancelled()


async def test_runner_survives_a_jump_far_into_the_past(service, clock, bus):
    service.create("pasta", 600)
    task = await running(service)
    try:
        clock.jump(days=-365 * 30)
        await asyncio.sleep(0.1)
        assert not task.done()  # no crash, no busy loop error
        assert [t.state for t in service.list_active()] == ["running"]
    finally:
        task.cancel()
