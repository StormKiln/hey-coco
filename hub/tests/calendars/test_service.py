import logging
import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from coco_hub import db
from coco_hub.calendar.model import (
    AuthRequired,
    CalendarEvent,
    CalendarRef,
    FetchResult,
    Unreachable,
)
from coco_hub.calendar.service import POLL_EVERY, STALE_AFTER, CalendarService
from coco_hub.settings import Settings

from conftest import TEST_TOKEN

pytestmark = pytest.mark.anyio

TZ = ZoneInfo("America/Chicago")
# 10:00 on Tuesday 2026-11-24 in Chicago.
NOW = datetime(2026, 11, 24, 16, 0, tzinfo=UTC)


def at(d: int, h: int = 0, mi: int = 0, mo: int = 11) -> datetime:
    return datetime(2026, mo, d, h, mi, tzinfo=TZ)


def ev(title: str, start: datetime, hours: float = 1, *, all_day=False, busy=False, uid=None):
    return CalendarEvent(
        id=uid or f"{title}-{start.isoformat()}",
        title=title,
        start=start,
        end=start + timedelta(hours=hours),
        all_day=all_day,
        calendar=CalendarRef(name=""),
        busy_only=busy,
    )


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class FakeSource:
    """Stands in for any source: returns ``events`` or raises ``error``."""

    def __init__(self, name: str | None, events: list[CalendarEvent]) -> None:
        self.name = name
        self.events = events
        self.error: Exception | None = None
        self.windows: list[tuple[datetime, datetime]] = []
        self.closed = False

    async def fetch(self, start: datetime, end: datetime) -> FetchResult:
        self.windows.append((start, end))
        if self.error is not None:
            raise self.error
        return FetchResult(
            name=self.name,
            events=[e for e in self.events if e.end > start and e.start < end],
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "data")
    yield c
    c.close()


@pytest.fixture
def feeds() -> dict[str, FakeSource]:
    """Fake sources keyed by the configured ``url``."""
    return {
        "https://a.example.invalid/family.ics": FakeSource(
            "Family",
            [
                ev("Soccer practice", at(24, 18)),
                ev("School holiday", at(27), 24, all_day=True),
                ev("Breakfast", at(24, 7)),
            ],
        ),
        "https://b.example.invalid/work.ics": FakeSource(
            "Work", [ev("Standup", at(24, 9, 30), 0.25)]
        ),
    }


def cal(url: str, **extra) -> dict:
    return {"source": "ics", "url": url, **extra}


@pytest.fixture
def make(settings, clock, conn, feeds):
    def build(calendars=None, *, connection=conn, rng=None) -> CalendarService:
        if calendars is None:
            calendars = [cal(u) for u in feeds]
        s = Settings(
            token=TEST_TOKEN,
            data_dir=settings.data_dir,
            discovery={"enabled": False},
            timezone="America/Chicago",
            calendars=calendars,
        )
        return CalendarService(
            s,
            db=connection,
            clock=clock,
            source_factory=lambda config, tz: feeds[config.options.url.get_secret_value()],
            rng=rng or random.Random(0),
        )

    return build


async def test_no_calendars_configured(make):
    service = make([])
    assert not service.configured
    assert await service.refresh() is None
    assert service.view() is None


async def test_refresh_merges_calendars_in_start_order(make):
    service = make()
    await service.refresh()
    view = service.view()
    assert [e.title for e in view.events] == [
        "Breakfast",
        "Standup",
        "Soccer practice",
        "School holiday",
    ]
    assert not view.stale
    assert view.fetched_at == NOW


async def test_window_is_one_day_back_to_fourteen_days_ahead_in_hub_time(make, feeds):
    await make().refresh()
    (window,) = feeds["https://a.example.invalid/family.ics"].windows
    assert window == (at(23), at(9, mo=12))  # local midnights: yesterday .. 15 days from today


async def test_names_come_from_config_then_feed_then_position(make, feeds):
    feeds["https://b.example.invalid/work.ics"].name = None
    service = make(
        [
            cal("https://a.example.invalid/family.ics", name="Home"),
            cal("https://b.example.invalid/work.ics"),
        ]
    )
    await service.refresh()
    names = {e.title: e.calendar.name for e in service.view().events}
    assert names["Breakfast"] == "Home"
    assert names["Standup"] == "Calendar 2"

    feeds["https://b.example.invalid/work.ics"].name = "Work"
    service = make()
    await service.refresh()
    assert {e.calendar.name for e in service.view().events} == {"Family", "Work"}


async def test_colours_come_from_config_or_a_default_per_position(make):
    service = make(
        [
            cal("https://a.example.invalid/family.ics", color="#123ABC"),
            cal("https://b.example.invalid/work.ics"),
        ]
    )
    await service.refresh()
    colours = {e.calendar.name: e.calendar.color for e in service.view().events}
    assert colours["Family"] == "#123ABC"
    assert colours["Work"].startswith("#") and len(colours["Work"]) == 7


async def test_busy_only_calendar_hides_titles(make):
    service = make(
        [
            cal("https://a.example.invalid/family.ics"),
            cal("https://b.example.invalid/work.ics", busy_only=True),
        ]
    )
    await service.refresh()
    work = [e for e in service.view().events if e.calendar.name == "Work"]
    assert [(e.title, e.busy_only) for e in work] == [("Busy", True)]
    assert not any(e.busy_only for e in service.view().events if e.calendar.name == "Family")


async def test_idle_view_leaves_out_calendars_not_shown_on_idle(make):
    service = make(
        [
            cal("https://a.example.invalid/family.ics"),
            cal("https://b.example.invalid/work.ics", show_on_idle=False),
        ]
    )
    await service.refresh()
    assert "Standup" in {e.title for e in service.view().events}
    assert "Standup" not in {e.title for e in service.view(idle=True).events}


async def test_events_between_returns_overlapping_events(make):
    service = make()
    await service.refresh()
    titles = [e.title for e in service.view().between(at(27), at(28))]
    assert titles == ["School holiday"]


async def test_failure_keeps_last_good_events_marked_stale(make, feeds, clock, caplog):
    service = make()
    await service.refresh()
    feeds["https://a.example.invalid/family.ics"].error = Unreachable()
    clock.advance(minutes=15)
    caplog.set_level(logging.INFO)
    await service.refresh()
    view = service.view()
    assert "Soccer practice" in {e.title for e in view.events}
    assert view.stale
    assert view.fetched_at == NOW  # the oldest calendar's last good fetch
    family, work = service.status()
    assert (family.stale, family.fetched_at, family.error) == (True, NOW, "unreachable")
    assert (work.stale, work.fetched_at, work.error) == (False, clock.now, None)
    assert "calendar 1" in caplog.text and "unreachable" in caplog.text
    assert "example.invalid" not in caplog.text


async def test_never_fetched_calendar_is_stale_with_no_events(make, feeds):
    feeds["https://a.example.invalid/family.ics"].error = AuthRequired()
    service = make()
    await service.refresh()
    view = service.view()
    assert [e.title for e in view.events] == ["Standup"]
    assert view.stale
    family, _ = service.status()
    assert (family.fetched_at, family.error) == (None, "auth_required")


async def test_old_data_is_stale_even_without_a_failure(make, clock):
    service = make()
    await service.refresh()
    clock.advance(seconds=STALE_AFTER.total_seconds() + 1)
    assert service.view().stale


async def test_last_good_data_survives_a_restart(make, feeds, conn, clock):
    await make().refresh()
    clock.advance(minutes=5)
    for feed in feeds.values():
        feed.error = Unreachable()
    restarted = make()
    view = restarted.view()  # before any fetch
    assert [e.title for e in view.events] == [
        "Breakfast",
        "Standup",
        "Soccer practice",
        "School holiday",
    ]
    assert view.fetched_at == NOW
    assert not view.stale
    await restarted.refresh()
    assert restarted.view().stale
    assert len(restarted.view().events) == 4


async def test_cache_follows_config_changes(make, feeds):
    await make().refresh()
    only_work = make([cal("https://b.example.invalid/work.ics", name="Office")])
    assert [(e.title, e.calendar.name) for e in only_work.view().events] == [("Standup", "Office")]


async def test_poll_interval_has_jitter(make):
    service = make(rng=random.Random(1))
    delays = {service.next_delay() for _ in range(50)}
    assert len(delays) > 1
    lo, hi = POLL_EVERY * 0.9, POLL_EVERY * 1.1
    assert all(lo <= d <= hi for d in delays)


async def test_aclose_closes_sources(make, feeds):
    service = make()
    await service.aclose()
    assert all(feed.closed for feed in feeds.values())
