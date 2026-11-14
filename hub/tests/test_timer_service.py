import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coco_hub import db
from coco_hub.events import EventBus
from coco_hub.protocol import TimerEvent
from coco_hub.timers.service import (
    TimerNotFound,
    TimerService,
    TimerStateError,
    normalize_timer_name,
)

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def service(conn, clock, bus) -> TimerService:
    return TimerService(conn, clock=clock, bus=bus)


def drain(sub) -> list[TimerEvent]:
    out = []
    while (event := sub.get_nowait()) is not None:
        out.append(event)
    return out


# --- names -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pasta", "pasta"),
        ("The Pasta Timer", "pasta"),
        ("the pasta", "pasta"),
        ("my eggs timer", "eggs"),
        ("  Eggs  ", "eggs"),
        ("timer", None),
        ("the timer", None),
        ("", None),
        (None, None),
        ("timer 2", "timer 2"),
        ("a pizza", "pizza"),
    ],
)
def test_normalize_timer_name(raw, expected):
    assert normalize_timer_name(raw) == expected


# --- create / get / list -----------------------------------------------------------------


def test_create_stores_absolute_end(service, clock):
    timer = service.create("The Pasta timer", 600)
    assert timer.id.startswith("t_")
    assert timer.name == "pasta"
    assert timer.duration_s == 600
    assert timer.created_at == T0
    assert timer.ends_at == T0 + timedelta(seconds=600)
    assert timer.state == "running"
    assert service.get(timer.id) == timer


def test_create_truncates_to_whole_seconds(service, clock):
    clock.now = T0 + timedelta(microseconds=700_000)
    timer = service.create("pasta", 60)
    assert timer.created_at == T0
    assert timer.ends_at == T0 + timedelta(seconds=60)


def test_create_rejects_non_positive_duration(service):
    with pytest.raises(ValueError):
        service.create("pasta", 0)


def test_default_names(service):
    first = service.create(None, 60)
    second = service.create(None, 60)
    third = service.create("", 60)
    assert [first.name, second.name, third.name] == ["timer", "timer 2", "timer 3"]


def test_default_name_reuses_lowest_free_slot(service):
    first = service.create(None, 60)
    service.create(None, 60)
    service.cancel(first.id)
    assert service.create(None, 60).name == "timer"


def test_duplicate_name_gets_a_number(service):
    service.create("pasta", 60)
    assert service.create("pasta", 60).name == "pasta 2"


def test_list_active_is_running_and_ringing_ordered_by_end(service, clock):
    long = service.create("long", 600)
    short = service.create("short", 60)
    gone = service.create("gone", 30)
    service.cancel(gone.id)
    assert [t.id for t in service.list_active()] == [short.id, long.id]
    clock.advance(seconds=61)
    service.tick()
    assert [(t.id, t.state) for t in service.list_active()] == [
        (short.id, "ringing"),
        (long.id, "running"),
    ]


def test_get_unknown_is_none(service):
    assert service.get("t_nope") is None


def test_find_by_name(service):
    pasta = service.create("pasta", 60)
    eggs = service.create("eggs", 60)
    assert service.find_by_name("the pasta timer") == pasta
    assert service.find_by_name("egg") == eggs  # plural-insensitive
    assert service.find_by_name("rice") is None
    service.cancel(pasta.id)
    assert service.find_by_name("pasta") is None


def test_persists_across_service_instances(tmp_path, clock):
    conn = db.open_db(tmp_path)
    timer = TimerService(conn, clock=clock).create("pasta", 600)
    conn.close()

    conn = db.open_db(tmp_path)
    try:
        again = TimerService(conn, clock=clock)
        assert again.get(timer.id) == timer
        assert again.list_active() == [timer]
    finally:
        conn.close()


# --- add time / cancel / dismiss ---------------------------------------------------------


def test_add_time_to_running(service, clock):
    timer = service.create("pasta", 600)
    clock.advance(seconds=100)
    updated = service.add_time(timer.id, 300)
    assert updated.duration_s == 900
    assert updated.ends_at == T0 + timedelta(seconds=900)
    assert updated.state == "running"


def test_add_time_to_ringing_restarts_from_now(service, clock):
    timer = service.create("pasta", 600)
    clock.advance(seconds=620)
    service.tick()
    updated = service.add_time(timer.id, 120)
    assert updated.state == "running"
    assert updated.ends_at == clock.now + timedelta(seconds=120)
    assert updated.duration_s == 620 + 120


def test_add_time_to_finished_timer_is_an_error(service):
    timer = service.create("pasta", 600)
    service.cancel(timer.id)
    with pytest.raises(TimerStateError):
        service.add_time(timer.id, 60)


def test_cancel(service):
    timer = service.create("pasta", 600)
    cancelled = service.cancel(timer.id)
    assert cancelled.state == "cancelled"
    assert service.list_active() == []
    # idempotent
    assert service.cancel(timer.id) == cancelled


def test_cancel_unknown(service):
    with pytest.raises(TimerNotFound):
        service.cancel("t_nope")


def test_cancel_all(service):
    a = service.create("a", 60)
    b = service.create("b", 60)
    cancelled = service.cancel_all()
    assert {t.id for t in cancelled} == {a.id, b.id}
    assert all(t.state == "cancelled" for t in cancelled)
    assert service.list_active() == []


def test_dismiss_ringing(service, clock):
    timer = service.create("pasta", 60)
    clock.advance(seconds=60)
    service.tick()
    dismissed = service.dismiss(timer.id)
    assert dismissed.state == "dismissed"
    assert service.dismiss(timer.id) == dismissed  # idempotent


def test_dismiss_running_is_an_error(service):
    timer = service.create("pasta", 60)
    with pytest.raises(TimerStateError):
        service.dismiss(timer.id)


def test_dismiss_all_ringing_leaves_running(service, clock):
    a = service.create("a", 60)
    b = service.create("b", 60)
    c = service.create("c", 600)
    clock.advance(seconds=60)
    service.tick()
    dismissed = service.dismiss_ringing()
    assert {t.id for t in dismissed} == {a.id, b.id}
    assert [t.id for t in service.list_active()] == [c.id]


# --- firing ------------------------------------------------------------------------------


def test_tick_fires_due_timers(service, clock):
    timer = service.create("pasta", 600)
    clock.advance(seconds=599)
    assert service.tick() == T0 + timedelta(seconds=600)  # next wake
    assert service.get(timer.id).state == "running"
    clock.advance(seconds=1)
    service.tick()
    assert service.get(timer.id).state == "ringing"


def test_tick_returns_none_when_idle(service):
    assert service.tick() is None


def test_ringing_auto_dismisses_after_cap(conn, clock, bus):
    service = TimerService(conn, clock=clock, bus=bus, ring_cap_s=600)
    timer = service.create("pasta", 60)
    clock.advance(seconds=60)
    assert service.tick() == clock.now + timedelta(seconds=600)
    clock.advance(seconds=599)
    service.tick()
    assert service.get(timer.id).state == "ringing"
    clock.advance(seconds=1)
    service.tick()
    assert service.get(timer.id).state == "dismissed"


def test_restart_while_down_recent_rings(tmp_path, clock):
    conn = db.open_db(tmp_path)
    timer = TimerService(conn, clock=clock).create("pasta", 600)
    conn.close()

    clock.advance(minutes=40)  # ended 30 minutes ago, Hub was down
    conn = db.open_db(tmp_path)
    try:
        service = TimerService(conn, clock=clock, missed_grace_s=3600, ring_cap_s=600)
        service.tick()
        assert service.get(timer.id).state == "ringing"
        # rings for the full cap from now, not from ends_at
        clock.advance(seconds=599)
        service.tick()
        assert service.get(timer.id).state == "ringing"
    finally:
        conn.close()


def test_restart_while_down_old_is_dismissed_silently(tmp_path, clock):
    conn = db.open_db(tmp_path)
    timer = TimerService(conn, clock=clock).create("pasta", 600)
    conn.close()

    clock.advance(hours=3)
    conn = db.open_db(tmp_path)
    try:
        bus = EventBus()
        service = TimerService(conn, clock=clock, bus=bus, missed_grace_s=3600)
        with bus.subscribe() as sub:
            service.tick()
            events = drain(sub)
        assert service.get(timer.id).state == "dismissed"
        assert [e.type for e in events] == ["timer.dismissed"]
    finally:
        conn.close()


# --- events ------------------------------------------------------------------------------


def test_every_change_publishes_an_event(service, clock, bus):
    with bus.subscribe() as sub:
        a = service.create("pasta", 60)
        service.add_time(a.id, 60)
        clock.advance(seconds=120)
        service.tick()
        service.dismiss(a.id)
        b = service.create("eggs", 60)
        service.cancel(b.id)
        service.cancel(b.id)  # no-op, no event
        events = drain(sub)
    assert [(e.type, e.timer.state) for e in events] == [
        ("timer.created", "running"),
        ("timer.updated", "running"),
        ("timer.fired", "ringing"),
        ("timer.dismissed", "dismissed"),
        ("timer.created", "running"),
        ("timer.cancelled", "cancelled"),
    ]
    assert events[2].server_time == clock.now


# --- background runner -------------------------------------------------------------------


@pytest.mark.anyio
async def test_runner_fires_when_woken(service, clock, bus):
    timer = service.create("pasta", 60)
    with bus.subscribe() as sub:
        task = asyncio.create_task(service.run())
        try:
            await asyncio.sleep(0.01)
            assert service.get(timer.id).state == "running"
            clock.advance(seconds=60)
            service.wake()
            event = await asyncio.wait_for(sub.get(), 1)
        finally:
            task.cancel()
    assert event.type == "timer.fired"
    assert event.timer.id == timer.id


@pytest.mark.anyio
async def test_runner_sleeps_until_due(conn, bus):
    # Real clock: a 1 s timer should fire on its own without a wake.
    service = TimerService(conn, bus=bus)
    timer = service.create("quick", 1)
    with bus.subscribe() as sub:
        task = asyncio.create_task(service.run())
        try:
            event = await asyncio.wait_for(sub.get(), 3)
        finally:
            task.cancel()
    assert (event.type, event.timer.id) == ("timer.fired", timer.id)
