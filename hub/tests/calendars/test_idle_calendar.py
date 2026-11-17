from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.calendar.service import CalendarService
from coco_hub.protocol import IdleInfo
from coco_hub.settings import Settings

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
# 10:00 on Friday 2026-11-27 in Chicago (the fixture's "School holiday").
FRIDAY = datetime(2026, 11, 27, 16, 0, tzinfo=UTC)
FAMILY = "https://calendar.example.invalid/family.ics"
ICS = (Path(__file__).resolve().parents[1] / "fixtures" / "calendar" / "family.ics").read_bytes()


class Feed:
    def __init__(self) -> None:
        self.requests = 0
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(200, content=ICS, headers={"Content-Type": "text/calendar"})


def make_settings(tmp_path, calendars, **extra) -> Settings:
    return Settings(
        token=TEST_TOKEN,
        data_dir=tmp_path / "data",
        discovery={"enabled": False},
        timezone="America/Chicago",
        calendars=calendars,
        **extra,
    )


def idle(settings: Settings, *, refresh: bool = True) -> tuple[IdleInfo, Feed]:
    feed = Feed()
    service = CalendarService(settings, clock=lambda: FRIDAY, transport=feed.transport)
    app = create_app(settings, clock=lambda: FRIDAY, calendar=service)
    with TestClient(app) as client:
        if refresh:
            client.portal.call(service.refresh)
        body = client.get("/v1/idle", headers=AUTH).json()
    return IdleInfo.model_validate(body), feed


def test_no_calendars_means_null(tmp_path):
    info, _ = idle(make_settings(tmp_path, []))
    assert info.calendar is None


def test_today_and_tomorrow_in_hub_dates(tmp_path):
    info, _ = idle(make_settings(tmp_path, [{"source": "ics", "url": FAMILY}]))
    cal = info.calendar
    assert [d.date for d in cal.days] == ["2026-11-27", "2026-11-28"]
    today, tomorrow = cal.days
    assert [e.title for e in today.events] == ["School holiday"]
    # All-day first, then by start; the overnight sleepover shows on the day it starts.
    assert [e.title for e in tomorrow.events] == ["Call with Grandma", "Sleepover"]
    holiday = today.events[0]
    assert holiday.all_day and holiday.calendar.name == "Family" and holiday.calendar.color
    assert not cal.stale and cal.fetched_at == FRIDAY and cal.hide_at_night is False


def test_multi_day_events_appear_on_each_day(tmp_path):
    settings = make_settings(tmp_path, [{"source": "ics", "url": FAMILY}])
    feed = Feed()
    saturday = datetime(2026, 12, 5, 16, 0, tzinfo=UTC)
    service = CalendarService(settings, clock=lambda: saturday, transport=feed.transport)
    with TestClient(create_app(settings, clock=lambda: saturday, calendar=service)) as client:
        client.portal.call(service.refresh)
        info = IdleInfo.model_validate(client.get("/v1/idle", headers=AUTH).json())
    assert [[e.title for e in d.events] for d in info.calendar.days] == [
        ["Camping trip"],
        ["Camping trip"],
    ]


def test_calendars_hidden_from_idle_are_left_out(tmp_path):
    settings = make_settings(tmp_path, [{"source": "ics", "url": FAMILY, "show_on_idle": False}])
    info, _ = idle(settings)
    assert info.calendar is not None
    assert all(d.events == [] for d in info.calendar.days)


def test_hide_at_night_comes_from_config(tmp_path):
    settings = make_settings(
        tmp_path, [{"source": "ics", "url": FAMILY}], night={"hide_calendar": True}
    )
    info, _ = idle(settings)
    assert info.calendar.hide_at_night is True


def test_idle_never_fetches_and_reports_missing_data_as_stale(tmp_path):
    settings = make_settings(tmp_path, [{"source": "ics", "url": FAMILY}])
    feed = Feed()
    service = CalendarService(settings, clock=lambda: FRIDAY, transport=feed.transport)
    service.run = _never  # no background poller in this test
    app = create_app(settings, clock=lambda: FRIDAY, calendar=service)
    with TestClient(app) as client:
        info = IdleInfo.model_validate(client.get("/v1/idle", headers=AUTH).json())
    assert feed.requests == 0
    assert info.calendar.stale and info.calendar.fetched_at is None
    assert all(d.events == [] for d in info.calendar.days)


async def _never() -> None:
    import asyncio

    await asyncio.Event().wait()


@pytest.mark.parametrize("hide", [False, True])
def test_night_setting_defaults_to_showing_events(tmp_path, hide):
    night = {"hide_calendar": hide} if hide else {}
    settings = make_settings(tmp_path, [], night=night)
    assert settings.night.hide_calendar is hide
