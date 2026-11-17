"""The normalised event every ``CalendarSource`` returns, and the errors it raises.

Times are timezone-aware and in the Hub's timezone. All-day events start and end at local
midnight, end exclusive (a one-day event on the 27th: 27th 00:00 -> 28th 00:00).
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CalendarRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    color: str | None = None
    """``#RRGGBB``."""


class CalendarEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    """Stable per occurrence, opaque."""
    title: str
    start: datetime
    end: datetime
    all_day: bool
    calendar: CalendarRef
    busy_only: bool = False
    """The title is hidden ("Busy"): the event is private, or its calendar is ``busy_only``."""


@dataclass(frozen=True)
class FetchResult:
    name: str | None
    """The calendar's own name from the feed (ICS ``X-WR-CALNAME``), if it has one."""
    events: list[CalendarEvent]
    """Occurrences overlapping the requested window, recurrences expanded, cancelled ones
    dropped. ``calendar`` is filled in by the service from config; sources leave it blank."""


class CalendarError(Exception):
    """A source couldn't deliver. Messages never contain URLs or credentials."""

    kind = "error"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.kind)


class Unreachable(CalendarError):
    """Network trouble or the server is unwell: try again later."""

    kind = "unreachable"


class AuthRequired(CalendarError):
    """The credentials or secret address were refused: someone has to fix the config."""

    kind = "auth_required"


class BadFeed(CalendarError):
    """An answer arrived but isn't a calendar we can read."""

    kind = "bad_feed"


BUSY = "Busy"
