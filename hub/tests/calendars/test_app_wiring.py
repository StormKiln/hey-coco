from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.calendar.service import CalendarService
from coco_hub.protocol import Diagnostics
from coco_hub.settings import Settings

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
FIXED = datetime(2026, 11, 24, 16, 0, tzinfo=UTC)
FAMILY = "https://calendar.example.invalid/ical/private-familysecret0123/basic.ics"
RESET = "https://calendar.example.invalid/ical/private-resetsecret4567/basic.ics"
ICS = (Path(__file__).resolve().parents[1] / "fixtures" / "calendar" / "family.ics").read_bytes()


def handler(request: httpx.Request) -> httpx.Response:
    if "familysecret" in str(request.url):
        return httpx.Response(200, content=ICS, headers={"Content-Type": "text/calendar"})
    return httpx.Response(404)


@pytest.fixture
def calendar_settings(tmp_path) -> Settings:
    return Settings(
        token=TEST_TOKEN,
        data_dir=tmp_path / "data",
        discovery={"enabled": False},
        timezone="America/Chicago",
        calendars=[{"source": "ics", "url": FAMILY}, {"source": "ics", "url": RESET}],
    )


def make_app(settings: Settings):
    service = CalendarService(settings, clock=lambda: FIXED, transport=httpx.MockTransport(handler))
    return create_app(settings, clock=lambda: FIXED, calendar=service), service


def test_configured_calendars_are_fetched_on_startup_and_reported(calendar_settings):
    app, service = make_app(calendar_settings)
    with TestClient(app) as client:
        assert app.state.services["calendar"] is service
        client.portal.call(service.refresh)  # the poller's first pass may still be running
        response = client.get("/v1/diagnostics", headers=AUTH)
    text = response.text
    assert "familysecret" not in text and "resetsecret" not in text
    assert "Family" not in text  # names stay out of diagnostics too
    health = Diagnostics.model_validate(response.json()).health
    family, reset = health.calendars
    assert (family.index, family.status, family.stale, family.fetched_at) == (1, "ok", False, FIXED)
    assert (reset.index, reset.status, reset.stale, reset.fetched_at) == (2, "error", True, None)
    assert reset.detail == "address or sign-in refused"


def test_cached_events_reach_the_service_database(calendar_settings):
    app, service = make_app(calendar_settings)
    with TestClient(app) as client:
        client.portal.call(service.refresh)
        rows = app.state.db.execute("SELECT COUNT(*) FROM calendar_cache").fetchone()[0]
    assert rows == 1  # the refused calendar has nothing to cache


def test_no_calendars_means_no_health_rows(settings):
    app = create_app(settings, clock=lambda: FIXED)
    with TestClient(app) as client:
        assert not app.state.services["calendar"].configured
        diag = client.get("/v1/diagnostics", headers=AUTH).json()
    assert diag["health"]["calendars"] == []
