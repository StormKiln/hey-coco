from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from coco_hub.settings import Settings, load_settings

from conftest import TEST_TOKEN

TOML = """
token = "from-file-token-0123456789abc"
port = 9000
timezone = "America/Toronto"
units = "imperial"

[location]
lat = 45.42
lon = -75.69
name = "Kitchen"

[night]
start = "22:00"

[qwen]
enabled = true
model = "qwen3:8b"

[keys]
youtube = "yt-key"

[spotify]
client_id = "sp-id"
"""


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = TOML) -> Path:
    path = tmp_path / "config.local.toml"
    path.write_text(body)
    monkeypatch.setenv("COCO_CONFIG", str(path))
    return path


def test_defaults_with_only_token():
    s = Settings(token=TEST_TOKEN)
    assert s.host == "0.0.0.0"
    assert s.port == 8765
    assert s.data_dir == Path.home() / "Library" / "Application Support" / "CocoHub"
    assert s.units == "metric"
    assert s.night.start == time(21, 30)
    assert s.night.end == time(6, 30)
    assert s.night.dark_after_idle_s == 300
    assert s.qwen.enabled is False
    assert s.keys.youtube is None
    assert s.spotify.client_id is None
    assert s.discovery.enabled is True


def test_missing_token_fails():
    with pytest.raises(ValidationError, match="token"):
        load_settings()


@pytest.mark.parametrize(
    "weak",
    ["change-me", "CHANGE-ME-change-me-change-me", "x" * 23, "changeme", "password", "token"],
)
def test_weak_or_placeholder_token_fails(weak):
    with pytest.raises(ValidationError, match="token"):
        Settings(token=weak)


def test_strong_token_accepted():
    assert Settings(token=TEST_TOKEN).token.get_secret_value() == TEST_TOKEN


def test_empty_token_fails():
    with pytest.raises(ValidationError):
        Settings(token="")


def test_loads_from_config_file(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch)
    s = load_settings()
    assert s.token.get_secret_value() == "from-file-token-0123456789abc"
    assert s.port == 9000
    assert s.timezone == "America/Toronto"
    assert s.units == "imperial"
    assert (s.location.lat, s.location.lon, s.location.name) == (45.42, -75.69, "Kitchen")
    assert s.night.start == time(22, 0)
    assert s.night.end == time(6, 30)
    assert s.qwen.enabled is True
    assert s.qwen.model == "qwen3:8b"
    assert s.keys.youtube.get_secret_value() == "yt-key"
    assert s.spotify.client_id == "sp-id"


def test_env_overrides_config_file(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch)
    monkeypatch.setenv("COCO_TOKEN", "from-env-token-0123456789abcd")
    monkeypatch.setenv("COCO_PORT", "9100")
    monkeypatch.setenv("COCO_NIGHT__START", "23:15")
    monkeypatch.setenv("COCO_QWEN__ENABLED", "false")
    s = load_settings()
    assert s.token.get_secret_value() == "from-env-token-0123456789abcd"
    assert s.port == 9100
    assert s.night.start == time(23, 15)
    assert s.qwen.enabled is False
    # nested values not overridden still come from the file
    assert s.qwen.model == "qwen3:8b"


def test_env_alone_is_enough(monkeypatch):
    monkeypatch.setenv("COCO_TOKEN", "env-only-token-0123456789abcd")
    assert load_settings().token.get_secret_value() == "env-only-token-0123456789abcd"


def test_data_dir_expands_user(monkeypatch):
    monkeypatch.setenv("COCO_DATA_DIR", "~/coco-data")
    s = Settings(token=TEST_TOKEN)
    assert s.data_dir == Path.home() / "coco-data"


@pytest.mark.parametrize(
    "field, value",
    [
        ("units", "kelvin"),
        ("timezone", "Mars/Olympus_Mons"),
        ("port", 0),
        ("night", {"start": "25:00"}),
        ("night", {"start": "9pm"}),
        ("night", {"start": "21:30:15"}),
        ("night", {"dark_after_idle_s": -1}),
        ("location", {"lat": 91, "lon": 0}),
        ("location", {"lat": 0, "lon": 181}),
    ],
)
def test_invalid_values_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(token="t", **{field: value})


def test_token_not_leaked_in_repr():
    s = Settings(token="super-secret-token-0123456789")
    assert "super-secret-token-0123456789" not in repr(s)


def test_example_config_is_valid(monkeypatch):
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    monkeypatch.setenv("COCO_CONFIG", str(example))
    monkeypatch.setenv("COCO_TOKEN", TEST_TOKEN)
    s = load_settings()
    assert s.port == 8765


def test_example_config_alone_refuses_to_start(monkeypatch):
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    monkeypatch.setenv("COCO_CONFIG", str(example))
    monkeypatch.delenv("COCO_TOKEN", raising=False)
    with pytest.raises(ValidationError, match="token"):
        load_settings()


def test_weather_forecast_url_defaults_to_open_meteo():
    from coco_hub.weather.client import FORECAST_URL

    assert Settings(token=TEST_TOKEN).weather.forecast_url == FORECAST_URL


def test_weather_forecast_url_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("COCO_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("COCO_WEATHER__FORECAST_URL", "http://127.0.0.1:9/v1/forecast")
    assert load_settings().weather.forecast_url == "http://127.0.0.1:9/v1/forecast"
