"""The contract every ``CalendarSource`` meets, as a reusable test suite.

A new source (CalDAV, the Google Calendar API, ...) gets the same coverage by subclassing
``CalendarSourceContract`` in a ``test_<source>.py`` and implementing two fixtures:

- ``make_source(scenario)``: a source serving the fictional scenario ``"family"`` or
  ``"spring"`` (``tests/fixtures/calendar/README.md`` describes them) in its own format —
  .ics files for ICS, recorded responses for others. Never the live network.
- ``failing_source(kind)``: a source whose fetch fails with ``"unreachable"``, ``"auth"``
  (credentials or secret URL no longer accepted) or ``"bad_feed"`` (answer not understood).

The Hub's timezone in every scenario is America/Chicago.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from coco_hub.calendar.model import AuthRequired, BadFeed, CalendarEvent, Unreachable
from coco_hub.calendar.sources import CalendarSource

pytestmark = pytest.mark.anyio

TZ = ZoneInfo("America/Chicago")


def at(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def rows(events: list[CalendarEvent]) -> list[tuple[str, str, bool]]:
    """(title, Hub-local start ``YYYY-MM-DDTHH:MM``, all_day), sorted."""
    return sorted((e.title, e.start.astimezone(TZ).isoformat()[:16], e.all_day) for e in events)


def one(events: list[CalendarEvent], title: str) -> CalendarEvent:
    matches = [e for e in events if e.title == title]
    assert len(matches) == 1, f"{title!r}: {len(matches)} matches"
    return matches[0]


MakeSource = Callable[[str], CalendarSource]


class CalendarSourceContract:
    """Subclass as ``Test<Source>Contract`` and provide the two fixtures."""

    @pytest.fixture
    def make_source(self) -> MakeSource:
        raise NotImplementedError

    @pytest.fixture
    def failing_source(self) -> Callable[[str], CalendarSource]:
        raise NotImplementedError

    async def fetch(self, source: CalendarSource, start: datetime, end: datetime):
        result = await source.fetch(start, end)
        for e in result.events:
            assert e.start.tzinfo is not None and e.end.tzinfo is not None
            assert e.start.utcoffset() == e.start.astimezone(TZ).utcoffset()  # Hub-local
            assert e.end >= e.start
        return result

    # --- the feed itself -------------------------------------------------------------------

    async def test_reports_the_feeds_own_calendar_name(self, make_source):
        assert (
            await self.fetch(make_source("family"), at(2026, 11, 23), at(2026, 11, 30))
        ).name == ("Family")

    async def test_ids_are_stable_and_unique_per_occurrence(self, make_source):
        first = await self.fetch(make_source("family"), at(2026, 10, 26), at(2026, 12, 31))
        again = await self.fetch(make_source("family"), at(2026, 10, 26), at(2026, 12, 31))
        ids = [e.id for e in first.events]
        assert len(ids) == len(set(ids))
        assert sorted(ids) == sorted(e.id for e in again.events)

    # --- recurrence ------------------------------------------------------------------------

    async def test_recurring_with_excluded_moved_and_cancelled_occurrences(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 11, 23), at(2026, 12, 20))
        ).events
        soccer = [r for r in rows(events) if r[0].startswith("Soccer")]
        assert soccer == [
            ("Soccer practice", "2026-11-24T18:00", False),
            # 12-01 excluded (EXDATE); 12-08 moved to 18:30 and retitled; 12-15 cancelled.
            ("Soccer practice (field 2)", "2026-12-08T18:30", False),
        ]
        moved = one(events, "Soccer practice (field 2)")
        assert moved.end == at(2026, 12, 8, 19, 30)

    async def test_window_only_returns_occurrences_that_overlap_it(self, make_source):
        events = (await self.fetch(make_source("family"), at(2026, 12, 8), at(2026, 12, 9))).events
        assert [e.title for e in events] == ["Soccer practice (field 2)"]

    # --- all-day ---------------------------------------------------------------------------

    async def test_all_day_event_covers_its_local_day_end_exclusive(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 11, 27), at(2026, 11, 28))
        ).events
        holiday = one(events, "School holiday")
        assert holiday.all_day
        assert (holiday.start, holiday.end) == (at(2026, 11, 27), at(2026, 11, 28))

    async def test_multi_day_all_day_event(self, make_source):
        events = (await self.fetch(make_source("family"), at(2026, 12, 5), at(2026, 12, 6))).events
        trip = one(events, "Camping trip")
        assert trip.all_day
        assert (trip.start, trip.end) == (at(2026, 12, 4), at(2026, 12, 7))

    async def test_overnight_timed_event_spans_two_days(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 11, 29), at(2026, 11, 30))
        ).events
        sleepover = one(events, "Sleepover")
        assert not sleepover.all_day
        assert (sleepover.start, sleepover.end) == (at(2026, 11, 28, 19), at(2026, 11, 29, 10))

    # --- time zones ------------------------------------------------------------------------

    async def test_other_timezone_is_converted_to_hub_time(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 11, 28), at(2026, 11, 29))
        ).events
        call = one(events, "Call with Grandma")  # 15:00 London
        assert (call.start, call.end) == (at(2026, 11, 28, 9), at(2026, 11, 28, 10))

    async def test_utc_event_is_converted_to_hub_time(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 11, 25), at(2026, 11, 26))
        ).events
        assert one(events, "Busy").start == at(2026, 11, 25, 14)  # the private dentist, 20:00Z

    async def test_daily_event_keeps_local_time_across_end_of_dst(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 10, 26), at(2026, 11, 10))
        ).events
        swims = [r[1] for r in rows(events) if r[0] == "Morning swim"]
        assert swims == [
            f"2026-{d}T07:30"
            for d in ("10-28", "10-29", "10-30", "10-31", "11-01", "11-02", "11-03", "11-04")
        ]

    async def test_weekly_event_keeps_local_time_across_start_of_dst(self, make_source):
        events = (await self.fetch(make_source("spring"), at(2027, 3, 1), at(2027, 3, 25))).events
        lessons = [e for e in events if e.title == "Swim lessons"]
        assert [e.start for e in lessons] == [at(2027, 3, 13, 9), at(2027, 3, 20, 9)]
        assert [e.start.utcoffset() for e in lessons] == [timedelta(hours=-6), timedelta(hours=-5)]

    async def test_floating_time_is_hub_local(self, make_source):
        events = (await self.fetch(make_source("spring"), at(2027, 3, 6), at(2027, 3, 7))).events
        assert one(events, "Book club").start == at(2027, 3, 6, 19)

    # --- cancelled and private -------------------------------------------------------------

    async def test_cancelled_events_are_dropped(self, make_source):
        events = (
            await self.fetch(make_source("family"), at(2026, 10, 26), at(2026, 12, 31))
        ).events
        assert "Piano lesson" not in {e.title for e in events}

    async def test_private_and_confidential_events_show_only_busy(self, make_source):
        family = (
            await self.fetch(make_source("family"), at(2026, 11, 25), at(2026, 11, 26))
        ).events
        spring = (await self.fetch(make_source("spring"), at(2027, 3, 9), at(2027, 3, 10))).events
        for event in (one(family, "Busy"), one(spring, "Busy")):
            assert event.busy_only
        everything = family + spring
        assert not {"Dentist", "Checkup"} & {e.title for e in everything}
        assert not any(e.busy_only for e in everything if e.title != "Busy")

    # --- failures --------------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("kind", "error"),
        [("unreachable", Unreachable), ("auth", AuthRequired), ("bad_feed", BadFeed)],
    )
    async def test_failures_raise_typed_errors(self, failing_source, kind, error):
        with pytest.raises(error):
            await failing_source(kind).fetch(at(2026, 11, 23), at(2026, 11, 30))
