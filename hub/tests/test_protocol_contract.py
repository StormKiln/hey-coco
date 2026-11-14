"""Contract tests: every fixture in docs/protocol/fixtures validates and round-trips."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from coco_hub import protocol as p

FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "protocol" / "fixtures"

# Filename prefix -> model. Longest prefix wins, so `timer_create_request` beats `timer_`.
PREFIXES: dict[str, type[BaseModel] | TypeAdapter] = {
    "utterance_request": p.UtteranceRequest,
    "action_": p.Action,
    "idle_info": p.IdleInfo,
    "timers_list": p.TimersList,
    "timer_create_request": p.TimerCreateRequest,
    "timer_": p.Timer,
    "event_": p.EVENT_ADAPTER,
    "error": p.ErrorResponse,
    "diagnostics": p.Diagnostics,
    "health": p.Health,
}


def model_for(name: str) -> type[BaseModel] | TypeAdapter:
    matches = [prefix for prefix in PREFIXES if name.startswith(prefix)]
    if not matches:
        raise LookupError(f"no model mapped for fixture {name}")
    return PREFIXES[max(matches, key=len)]


def fixture_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def test_fixtures_exist():
    assert len(fixture_files()) >= 30


@pytest.mark.parametrize("path", fixture_files(), ids=lambda path: path.stem)
def test_every_fixture_is_mapped(path: Path):
    model_for(path.stem)


@pytest.mark.parametrize("path", fixture_files(), ids=lambda path: path.stem)
def test_fixture_validates_and_round_trips(path: Path):
    raw = json.loads(path.read_text())
    target = model_for(path.stem)
    if isinstance(target, TypeAdapter):
        value = target.validate_python(raw)
        dumped = target.dump_python(value, mode="json")
    else:
        value = target.model_validate(raw)
        dumped = value.model_dump(mode="json")
    assert dumped == raw


def test_every_view_type_has_a_fixture():
    seen = {
        json.loads(path.read_text())["view"]["type"]
        for path in FIXTURES.glob("action_*.json")
        if json.loads(path.read_text())["view"] is not None
    }
    assert seen == set(p.ViewType.__args__)


def test_every_event_type_has_a_fixture():
    seen = {json.loads(path.read_text())["type"] for path in FIXTURES.glob("event_*.json")}
    expected = {"hello", "now_playing", "settings.changed"} | {
        f"timer.{kind}" for kind in ("created", "updated", "fired", "dismissed", "cancelled")
    }
    assert seen == expected


def test_view_is_discriminated_by_type():
    action = p.Action.model_validate_json((FIXTURES / "action_weather.json").read_text())
    assert isinstance(action.view, p.WeatherView)
    assert action.view.hourly[0].precip_probability == 10


def test_commands_are_discriminated_by_type():
    action = p.Action.model_validate_json((FIXTURES / "action_commands.json").read_text())
    kinds = [type(command) for command in action.commands]
    assert kinds == [
        p.AlarmStop,
        p.MediaCommand,
        p.VolumeSet,
        p.VolumeChange,
        p.ScreenWake,
        p.ScreenSleep,
    ]


def test_events_are_discriminated_by_type():
    event = p.EVENT_ADAPTER.validate_json((FIXTURES / "event_timer_fired.json").read_text())
    assert isinstance(event, p.TimerEvent)
    assert event.timer.state == "ringing"


def test_unknown_view_type_is_rejected():
    with pytest.raises(ValidationError):
        p.Action.model_validate(
            {
                "say": None,
                "view": {"type": "hologram"},
                "commands": [],
                "intent": "none",
                "matched_by": "none",
            }
        )


def test_volume_level_is_bounded():
    with pytest.raises(ValidationError):
        p.VolumeSet(level=101)


def test_datetimes_serialize_as_utc_with_z():
    eastern = timezone(timedelta(hours=-5))
    timer = p.Timer(
        id="t_1",
        name=None,
        duration_s=60,
        created_at=datetime(2026, 11, 14, 10, 0, 0, 123456, tzinfo=eastern),
        ends_at=datetime(2026, 11, 14, 15, 1, 0, tzinfo=UTC),
        state="running",
    )
    dumped = timer.model_dump(mode="json")
    assert dumped["created_at"] == "2026-11-14T15:00:00Z"
    assert dumped["ends_at"] == "2026-11-14T15:01:00Z"


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValidationError):
        p.Timer(
            id="t_1",
            name=None,
            duration_s=60,
            created_at=datetime(2026, 11, 14, 15, 0, 0),
            ends_at=datetime(2026, 11, 14, 15, 1, 0, tzinfo=UTC),
            state="running",
        )
