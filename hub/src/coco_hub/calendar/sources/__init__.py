"""Calendar sources: where events come from.

Adding a source (iCloud CalDAV, the Google Calendar API, ...) is a new module here plus a
line in ``SOURCES``; the service, intents, protocol and Display don't change:

- an ``Options`` pydantic model with the source's own config keys (secrets as ``SecretStr``,
  ``extra="forbid"``), read from a ``[[calendars]]`` entry with ``source = "<name>"``;
- a class implementing ``CalendarSource``, built as ``Source(options, tz=..., transport=...)``;
- a subclass of ``tests/calendar/contract.py``'s ``CalendarSourceContract`` with fixtures in
  the source's own format.

Read-only for now; writing events would be a separate, optional interface.
"""

import importlib
from datetime import datetime
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel

from coco_hub.calendar.model import FetchResult

SOURCES: dict[str, str] = {
    "ics": "coco_hub.calendar.sources.ics:IcsSource",
}
"""``source = "<name>"`` -> ``"module:Class"``, imported on first use."""


@runtime_checkable
class CalendarSource(Protocol):
    async def fetch(self, start: datetime, end: datetime) -> FetchResult:
        """Occurrences overlapping ``[start, end)``, in the Hub's timezone.

        Raises ``Unreachable``, ``AuthRequired`` or ``BadFeed`` (``coco_hub.calendar.model``).
        """
        ...

    async def aclose(self) -> None: ...


class SourceClass(Protocol):
    Options: type[BaseModel]

    def __call__(
        self,
        options: BaseModel,
        *,
        tz: ZoneInfo,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> CalendarSource: ...


def source_class(name: str) -> SourceClass:
    """The class for ``source = name``. Raises ``KeyError`` for an unknown source."""
    module, _, cls = SOURCES[name].partition(":")
    return getattr(importlib.import_module(module), cls)
