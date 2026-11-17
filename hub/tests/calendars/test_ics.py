import asyncio
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr

from coco_hub.calendar.model import AuthRequired, BadFeed, Unreachable
from coco_hub.calendar.sources.ics import IcsOptions, IcsSource

from .contract import TZ, CalendarSourceContract, at

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "calendar"
SECRET = "https://calendar.example.invalid/ical/private-0123456789abcdef/basic.ics"


class Feed:
    """Serves an .ics fixture; records requests. ``status``/``body`` override the answer."""

    def __init__(self, scenario: str = "family") -> None:
        self.body: bytes = (FIXTURES / f"{scenario}.ics").read_bytes()
        self.status = 200
        self.content_type = "text/calendar; charset=utf-8"
        self.headers: dict[str, str] = {}
        self.error: Exception | None = None
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if (
            self.headers.get("ETag")
            and request.headers.get("If-None-Match") == self.headers["ETag"]
        ):
            return httpx.Response(304, headers=self.headers)
        return httpx.Response(
            self.status,
            content=self.body,
            headers={"Content-Type": self.content_type, **self.headers},
        )


def make(feed: Feed, url: str = SECRET) -> IcsSource:
    return IcsSource(IcsOptions(url=SecretStr(url)), tz=TZ, transport=feed.transport)


class TestIcsContract(CalendarSourceContract):
    @pytest.fixture
    def make_source(self):
        return lambda scenario: make(Feed(scenario))

    @pytest.fixture
    def failing_source(self):
        def build(kind: str) -> IcsSource:
            feed = Feed()
            if kind == "unreachable":
                feed.error = httpx.ConnectError("offline")
            elif kind == "auth":
                feed.status = 404  # Google's answer once a secret address is reset
            else:
                feed.body, feed.content_type = b"<html>Sign in</html>", "text/html"
            return make(feed)

        return build


async def test_webcal_urls_are_fetched_over_https():
    feed = Feed()
    await make(feed, SECRET.replace("https://", "webcal://")).fetch(
        at(2026, 11, 23), at(2026, 11, 24)
    )
    assert feed.requests[0].url.scheme == "https"


async def test_conditional_request_reuses_the_last_feed_for_a_new_window():
    feed = Feed()
    feed.headers = {"ETag": '"v1"', "Last-Modified": "Tue, 17 Nov 2026 12:00:00 GMT"}
    source = make(feed)
    await source.fetch(at(2026, 11, 23), at(2026, 11, 24))
    second = await source.fetch(at(2026, 11, 27), at(2026, 11, 28))
    assert feed.requests[1].headers["If-None-Match"] == '"v1"'
    assert feed.requests[1].headers["If-Modified-Since"] == "Tue, 17 Nov 2026 12:00:00 GMT"
    assert [e.title for e in second.events] == ["School holiday"]  # re-expanded, not re-sent


@pytest.mark.parametrize("status", [401, 403, 404, 410])
async def test_rejected_address_needs_attention(status):
    feed = Feed()
    feed.status = status
    with pytest.raises(AuthRequired):
        await make(feed).fetch(at(2026, 11, 23), at(2026, 11, 24))


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_server_trouble_is_unreachable(status):
    feed = Feed()
    feed.status = status
    with pytest.raises(Unreachable):
        await make(feed).fetch(at(2026, 11, 23), at(2026, 11, 24))


async def test_garbage_ics_is_a_bad_feed():
    feed = Feed()
    feed.body = b"BEGIN:VCALENDAR\r\nthis is not ical\r\n"
    with pytest.raises(BadFeed):
        await make(feed).fetch(at(2026, 11, 23), at(2026, 11, 24))


async def test_oversized_feed_is_refused():
    feed = Feed()
    feed.body = b"BEGIN:VCALENDAR\r\n" + b"X" * (21 * 1024 * 1024)
    with pytest.raises(BadFeed):
        await make(feed).fetch(at(2026, 11, 23), at(2026, 11, 24))


@pytest.mark.parametrize(
    "error", [httpx.ConnectError(f"cannot reach {SECRET}"), httpx.ReadTimeout(SECRET)]
)
async def test_the_secret_url_never_appears_in_errors_or_logs(error, caplog):
    feed = Feed()
    feed.error = error
    caplog.set_level(logging.DEBUG, logger="coco_hub")  # httpx: QUIET_LOGGERS
    with pytest.raises(Unreachable) as exc_info:
        await make(feed).fetch(at(2026, 11, 23), at(2026, 11, 24))
    secret_part = "private-0123456789abcdef"
    assert secret_part not in str(exc_info.value)
    assert secret_part not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None and exc_info.value.__suppress_context__
    assert secret_part not in caplog.text


def test_options_hide_the_url():
    options = IcsOptions(url=SecretStr(SECRET))
    assert "private-0123456789abcdef" not in repr(options)
    assert "private-0123456789abcdef" not in str(options.model_dump())


def test_hub_timezone_is_required():
    with pytest.raises(TypeError):
        IcsSource(IcsOptions(url=SecretStr(SECRET)))  # type: ignore[call-arg]
    assert IcsSource(IcsOptions(url=SecretStr(SECRET)), tz=ZoneInfo("UTC"))


async def test_runaway_recurrence_is_a_bad_feed():
    feed = Feed()
    feed.body = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:tick@example.invalid\r\n"
        b"SUMMARY:Tick\r\nDTSTART:20261123T000000Z\r\nDTEND:20261123T000001Z\r\n"
        b"RRULE:FREQ=SECONDLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    with pytest.raises(BadFeed):
        await make(feed).fetch(at(2026, 11, 23), at(2026, 12, 7))


async def test_a_slow_trickle_hits_the_overall_deadline():
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"BEGIN:VCALENDAR\r\n"
            while True:
                await asyncio.sleep(0.01)
                yield b"X"

    headers = {"Content-Type": "text/calendar"}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Trickle(), headers=headers)
    )
    options = IcsOptions(url=SecretStr(SECRET))
    source = IcsSource(options, tz=TZ, transport=transport, deadline_s=0.1)
    with pytest.raises(Unreachable):
        await source.fetch(at(2026, 11, 23), at(2026, 11, 24))
