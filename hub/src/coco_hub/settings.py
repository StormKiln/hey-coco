"""Typed Hub settings.

Sources, highest priority first:
1. keyword arguments to ``Settings(...)`` (tests),
2. ``COCO_*`` environment variables (nested fields use ``__``, e.g. ``COCO_NIGHT__START``),
3. the TOML file at ``$COCO_CONFIG`` (default ``hub/config.local.toml``).
"""

import os
import re
from datetime import time
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

HUB_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = HUB_DIR / "config.local.toml"
DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "CocoHub"
DEFAULT_SOUNDS_DIR = HUB_DIR.parent / "assets" / "sounds"
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "CocoHub"

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def config_path() -> Path:
    return Path(os.environ.get("COCO_CONFIG") or DEFAULT_CONFIG_PATH).expanduser()


def _parse_hhmm(value: object) -> object:
    if isinstance(value, str):
        if not _HHMM.match(value):
            raise ValueError("must be HH:MM (24-hour)")
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))
    return value


class Location(BaseModel):
    lat: Annotated[float, Field(ge=-90, le=90)] | None = None
    lon: Annotated[float, Field(ge=-180, le=180)] | None = None
    name: str = ""


class Night(BaseModel):
    start: time = time(21, 30)
    end: time = time(6, 30)
    dark_after_idle_s: Annotated[int, Field(ge=0)] = 300

    _hhmm = field_validator("start", "end", mode="before")(_parse_hhmm)


class Qwen(BaseModel):
    """Local LLM fallback via Ollama (see docs/spikes/qwen.md)."""

    enabled: bool = False
    model: str = "qwen3:8b"
    base_url: str = "http://localhost:11434"
    timeout_s: Annotated[float, Field(gt=0, le=60)] = 6.0
    """Hard limit on the whole fallback (model calls and one repair)."""


class Keys(BaseModel):
    youtube: SecretStr | None = None


class YouTube(BaseModel):
    region: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] = "CA"
    """ISO 3166-1 alpha-2 region for search results."""
    daily_quota: Annotated[int, Field(ge=0, le=10_000)] = 9000
    """Refuse searches past this many API units per day (a search costs 100; YouTube's free
    quota is 10,000/day, reset at midnight Pacific)."""


class Spotify(BaseModel):
    """Spotify Web API (see docs/spotify.md). Without ``client_id`` Spotify stays off."""

    client_id: str | None = None
    client_secret: SecretStr | None = None
    """Unused: the Hub logs in with Authorization Code + PKCE, which needs no secret. Kept so
    an existing config with one still loads."""
    redirect_uri: str | None = None
    """Must match the app's redirect URI on developer.spotify.com exactly. Default:
    ``https://127.0.0.1:<port>/spotify/callback`` (``http://`` with ``tls.enabled = false``;
    Spotify wants a loopback IP literal, not ``localhost``)."""
    device_name: str | None = None
    """The Spotify Connect device to play on, e.g. "Kitchen iPad" (as the Spotify app names
    it). Unset: the active device, or the only one."""
    wake_wait_s: Annotated[float, Field(ge=0, le=30)] = 4.0
    """When the iPad is missing from Spotify's device list, the Hub asks the Display to open
    the Spotify app (``spotify.wake_requested``) and waits this long before listing devices
    again. 0 turns waking off. Under Guided Access the Display can't open Spotify, so this is
    only extra delay there (docs/spotify.md, spike #5)."""


class Music(BaseModel):
    """Which service plays music (#42, docs/apple-music.md)."""

    provider: Literal["spotify", "apple_music"] = "spotify"
    """``spotify``: the Hub drives the Spotify app on the iPad over the Web API.
    ``apple_music``: the Hub tells the Display to search and play with MusicKit
    (``music.play`` command)."""


class Timers(BaseModel):
    ring_cap_s: Annotated[int, Field(gt=0)] = 600
    """A ringing timer is dismissed by the Hub after this long (nobody home)."""
    missed_grace_s: Annotated[int, Field(ge=0)] = 3600
    """A timer that ended while the Hub was down rings on startup if it ended at most this
    long ago; older ones are dismissed silently."""


class Weather(BaseModel):
    forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    """Open-Meteo forecast endpoint. Only changed by tests (pointed at a dead port to simulate
    the internet being down)."""
    geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    """Open-Meteo geocoding endpoint, for places named in a question (#93)."""


class Discovery(BaseModel):
    enabled: bool = True
    name: str = "Coco Hub"


class Tls(BaseModel):
    """HTTPS/WSS on ``port`` with the Hub's self-signed certificate (``data_dir/tls``), which
    the Display pins when it pairs (#54)."""

    enabled: bool = True
    """Off serves plain HTTP/WS: for tests and local development only. The Display refuses
    to pair with, or send its token to, a Hub without TLS."""


class Diagnostics(BaseModel):
    log_transcripts: bool = True
    """Write what Coco heard (``text``) and said (``say``) to the log file. Off: utterance
    log lines keep only intent, timing and errors. The in-memory diagnostics buffer (last
    100 utterances, never persisted) keeps them either way."""


MIN_TOKEN_LENGTH = 24
_PLACEHOLDER_TOKENS = {"changeme", "password", "token", "secret", "test"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COCO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    token: Annotated[SecretStr, Field(min_length=1)]

    @field_validator("token")
    @classmethod
    def _strong_token(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw.lower().replace("-", "").replace("_", "") in _PLACEHOLDER_TOKENS or "changeme" in (
            raw.lower().replace("-", "")
        ):
            raise ValueError(
                "token is a placeholder; generate a real one (see config.example.toml)"
            )
        if len(raw) < MIN_TOKEN_LENGTH:
            raise ValueError(f"token must be at least {MIN_TOKEN_LENGTH} characters")
        return v

    host: str = "0.0.0.0"
    port: Annotated[int, Field(ge=1, le=65535)] = 8765
    data_dir: Path = DEFAULT_DATA_DIR
    sounds_dir: Path = DEFAULT_SOUNDS_DIR
    log_dir: Path = DEFAULT_LOG_DIR
    """JSON-lines log (``hub.jsonl``, rotated at 5 MB, 5 kept)."""
    location: Location = Location()
    timezone: str = "UTC"
    units: Literal["metric", "imperial"] = "metric"
    night: Night = Night()
    qwen: Qwen = Qwen()
    keys: Keys = Keys()
    youtube: YouTube = YouTube()
    spotify: Spotify = Spotify()
    music: Music = Music()
    discovery: Discovery = Discovery()
    tls: Tls = Tls()
    timers: Timers = Timers()
    weather: Weather = Weather()
    diagnostics: Diagnostics = Diagnostics()

    @field_validator("data_dir", "sounds_dir", "log_dir")
    @classmethod
    def _expand_paths(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml = TomlConfigSettingsSource(settings_cls, toml_file=config_path())
        return (init_settings, env_settings, toml)


def load_settings() -> Settings:
    """Load settings from the config file and environment. Fails if no token is set."""
    return Settings()  # type: ignore[call-arg]  # token comes from file/env
