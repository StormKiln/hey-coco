import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coco_hub.events import EventBus
from coco_hub.protocol import EVENT_ADAPTER, NowPlayingEvent

FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "protocol" / "fixtures"
NOW = datetime(2026, 11, 14, 15, 4, 5, tzinfo=UTC)


def event(n: int = 0) -> NowPlayingEvent:
    return NowPlayingEvent(server_time=NOW, now_playing=None)


def test_fan_out_to_every_subscriber():
    bus = EventBus()
    fired = EVENT_ADAPTER.validate_json((FIXTURES / "event_timer_fired.json").read_text())
    with bus.subscribe() as a, bus.subscribe() as b:
        assert bus.subscriber_count == 2
        bus.publish(fired)
        assert a.get_nowait() == fired
        assert b.get_nowait() == fired
        assert a.get_nowait() is None
    assert bus.subscriber_count == 0


def test_publish_without_subscribers_is_fine():
    EventBus().publish(event())


def test_unsubscribed_gets_nothing():
    bus = EventBus()
    with bus.subscribe() as sub:
        pass
    bus.publish(event())
    assert sub.get_nowait() is None


def test_slow_subscriber_is_dropped_others_unaffected():
    bus = EventBus(max_queue=2)
    with bus.subscribe() as slow, bus.subscribe() as fast:
        bus.publish(event())
        assert fast.get_nowait() is not None
        bus.publish(event())
        assert fast.get_nowait() is not None
        bus.publish(event())  # slow now has 3 pending > 2
        assert slow.dropped
        assert not fast.dropped
        assert bus.subscriber_count == 1
        assert fast.get_nowait() is not None


def test_publish_rejects_non_events():
    with pytest.raises(TypeError):
        EventBus().publish({"type": "timer.fired"})  # type: ignore[arg-type]


def test_events_serialize_to_the_protocol_shape():
    raw = (FIXTURES / "event_timer_updated.json").read_text()
    parsed = EVENT_ADAPTER.validate_json(raw)
    assert json.loads(EventBus.encode(parsed)) == json.loads(raw)


@pytest.mark.anyio
async def test_async_get_waits_for_publish():
    bus = EventBus()
    with bus.subscribe() as sub:
        waiter = asyncio.create_task(sub.get())
        await asyncio.sleep(0)
        bus.publish(event())
        assert await asyncio.wait_for(waiter, 1) == event()


@pytest.mark.anyio
async def test_async_get_returns_none_once_dropped():
    bus = EventBus(max_queue=1)
    with bus.subscribe() as sub:
        bus.publish(event())
        bus.publish(event())
        assert sub.dropped
        assert await asyncio.wait_for(sub.get(), 1) is None
