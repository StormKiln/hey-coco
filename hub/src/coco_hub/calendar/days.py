"""Group cached events by Hub-local day, in protocol shape (the idle screen and voice answers
share it)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from coco_hub import protocol as p
from coco_hub.calendar.model import CalendarEvent
from coco_hub.calendar.service import CalendarView


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)


def on_day(view: CalendarView, day: date, tz: ZoneInfo) -> list[CalendarEvent]:
    """Events overlapping ``day``: all-day first, then by start, then title."""
    start, end = day_bounds(day, tz)
    return sorted(view.between(start, end), key=lambda e: (not e.all_day, e.start, e.title))


def to_protocol(event: CalendarEvent) -> p.CalendarEvent:
    return p.CalendarEvent(
        id=event.id,
        title=event.title,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        calendar=p.EventCalendar(name=event.calendar.name, color=event.calendar.color),
        busy_only=event.busy_only,
    )


def protocol_days(view: CalendarView, days: list[date], tz: ZoneInfo) -> list[p.CalendarDay]:
    return [
        p.CalendarDay(date=day.isoformat(), events=[to_protocol(e) for e in on_day(view, day, tz)])
        for day in days
    ]
