"""Hub <-> Display protocol models (v1).

The contract is `docs/protocol/README.md`; every fixture in `docs/protocol/fixtures/` must
validate against these models and round-trip unchanged (`tests/test_protocol_contract.py`).
Change the README, the fixtures, these models and the Swift models together.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
)


def _to_utc_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Timezone-aware on input; always serialized as UTC, whole seconds, `Z` suffix.
UtcDatetime = Annotated[AwareDatetime, PlainSerializer(_to_utc_z, return_type=str)]
# `HH:MM`, 24-hour, Hub-local wall clock.
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
# `YYYY-MM-DD`, Hub-local calendar date.
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Percent = Annotated[int, Field(ge=0, le=100)]
Seconds = Annotated[int, Field(ge=0)]

ViewType = Literal[
    "idle", "answer", "timers", "weather", "picture", "sound", "youtube", "now_playing"
]
MediaTarget = Literal["youtube", "music", "sound"]
TimerState = Literal["running", "ringing", "dismissed", "cancelled"]
TemperatureUnit = Literal["C", "F"]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Utterance --------------------------------------------------------------------------


class UtteranceContext(Message):
    view: ViewType
    alarm_ringing: bool
    media_playing: MediaTarget | None


class UtteranceRequest(Message):
    text: str
    source: Literal["voice", "touch"]
    context: UtteranceContext


# --- Weather ----------------------------------------------------------------------------


class WeatherNow(Message):
    time: UtcDatetime
    temp: float
    feels_like: float
    unit: TemperatureUnit
    code: int
    description: str
    is_day: bool


class WeatherHour(Message):
    time: UtcDatetime
    temp: float
    unit: TemperatureUnit
    code: int
    precip_probability: Percent


class WeatherDay(Message):
    date: IsoDate
    high: float
    low: float
    unit: TemperatureUnit
    code: int
    precip_probability: Percent


# --- Views ------------------------------------------------------------------------------


class Image(Message):
    url: str
    thumb_url: str
    title: str
    width: int
    height: int
    license: str
    author: str | None
    source_url: str


class IdleView(Message):
    type: Literal["idle"] = "idle"


class AnswerView(Message):
    type: Literal["answer"] = "answer"
    text: str


class TimersView(Message):
    type: Literal["timers"] = "timers"


class WeatherComparison(Message):
    """Home's weather beside a named place's (#111)."""

    name: str
    """What to call it on screen: ``Home``."""
    current: WeatherNow
    """Home's conditions now (the same instant as the place's ``current``)."""
    daily: list[WeatherDay]
    """Home's days for the place's ``daily`` dates (the place's local calendar dates), as far
    as home's forecast reaches; match them by ``date``."""


class WeatherView(Message):
    type: Literal["weather"] = "weather"
    current: WeatherNow
    hourly: list[WeatherHour]
    daily: list[WeatherDay]
    place: str | None = None
    """A place the person named (``Portland, Oregon``); null for home."""
    compare: WeatherComparison | None = None
    """Home's weather for the same periods, when ``place`` is set, home is known, and the
    place isn't home; otherwise null."""


class PictureView(Message):
    type: Literal["picture"] = "picture"
    query: str
    images: list[Image]


class SoundView(Message):
    type: Literal["sound"] = "sound"
    name: str
    clip_url: str
    image_url: str
    attribution: str
    caption: str | None = None
    """What the sound is, for the screen ("A cow says moo!"). Never spoken: sound views come
    with ``say: null`` so speech doesn't talk over the clip (#94)."""


class YoutubeView(Message):
    type: Literal["youtube"] = "youtube"
    video_id: str
    title: str


class NowPlayingView(Message):
    type: Literal["now_playing"] = "now_playing"


View = Annotated[
    IdleView
    | AnswerView
    | TimersView
    | WeatherView
    | PictureView
    | SoundView
    | YoutubeView
    | NowPlayingView,
    Field(discriminator="type"),
]


# --- Commands ---------------------------------------------------------------------------


class AlarmStop(Message):
    type: Literal["alarm.stop"] = "alarm.stop"


class MediaCommand(Message):
    type: Literal["media.pause", "media.resume", "media.next", "media.stop"]
    target: MediaTarget


class VolumeSet(Message):
    type: Literal["volume.set"] = "volume.set"
    level: Percent


class VolumeChange(Message):
    type: Literal["volume.change"] = "volume.change"
    delta: Annotated[int, Field(ge=-100, le=100)]


class ScreenWake(Message):
    type: Literal["screen.wake"] = "screen.wake"


class ScreenSleep(Message):
    type: Literal["screen.sleep"] = "screen.sleep"


MusicKind = Literal["artist", "album", "playlist", "song", "any"]


class MusicPlay(Message):
    """Apple Music (#42): the Display searches the catalog and plays the best match.
    ``query`` "" resumes whatever the Display's music player last had."""

    type: Literal["music.play"] = "music.play"
    query: str
    kind: MusicKind


Command = Annotated[
    AlarmStop | MediaCommand | VolumeSet | VolumeChange | ScreenWake | ScreenSleep | MusicPlay,
    Field(discriminator="type"),
]


# --- Action -----------------------------------------------------------------------------


class Action(Message):
    say: str | None
    view: View | None
    commands: list[Command]
    intent: str
    matched_by: Literal["rules", "qwen", "none"]


# --- Idle -------------------------------------------------------------------------------


class Night(Message):
    start: ClockTime
    end: ClockTime
    dark_after_idle_s: Seconds


class IdleWeather(Message):
    current: WeatherNow
    hourly: list[WeatherHour]
    stale: bool


class IdleInfo(Message):
    server_time: UtcDatetime
    timezone: str
    weather: IdleWeather | None
    night: Night


# --- Timers -----------------------------------------------------------------------------


class Timer(Message):
    id: str
    name: str | None
    duration_s: Seconds
    created_at: UtcDatetime
    ends_at: UtcDatetime
    state: TimerState


class TimersList(Message):
    timers: list[Timer]
    server_time: UtcDatetime


class TimerCreateRequest(Message):
    name: str | None
    duration_s: Annotated[int, Field(gt=0)]


class TimerAddRequest(Message):
    """``POST /v1/timers/{id}/add``: extend a running timer, or restart a ringing one."""

    seconds: Annotated[int, Field(gt=0)]


# --- Events -----------------------------------------------------------------------------


class NowPlaying(Message):
    source: Literal["spotify", "apple_music"]
    title: str
    artist: str
    album: str | None
    artwork_url: str | None
    is_playing: bool


class HelloEvent(Message):
    type: Literal["hello"] = "hello"
    server_time: UtcDatetime
    timers: list[Timer]
    now_playing: NowPlaying | None


class TimerEvent(Message):
    type: Literal[
        "timer.created", "timer.updated", "timer.fired", "timer.dismissed", "timer.cancelled"
    ]
    server_time: UtcDatetime
    timer: Timer


class NowPlayingEvent(Message):
    type: Literal["now_playing"] = "now_playing"
    server_time: UtcDatetime
    now_playing: NowPlaying | None


class SettingsChangedEvent(Message):
    type: Literal["settings.changed"] = "settings.changed"
    server_time: UtcDatetime
    night: Night


class SpotifyWakeRequestedEvent(Message):
    """The Hub can't see the iPad in Spotify's device list: asks the Display to open the
    Spotify app (#5). The Display does nothing under Guided Access."""

    type: Literal["spotify.wake_requested"] = "spotify.wake_requested"
    server_time: UtcDatetime
    device_name: str | None


Event = Annotated[
    HelloEvent | TimerEvent | NowPlayingEvent | SettingsChangedEvent | SpotifyWakeRequestedEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


# --- Errors -----------------------------------------------------------------------------


class ErrorBody(Message):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    message: str


class ErrorResponse(Message):
    error: ErrorBody


# --- Health -----------------------------------------------------------------------------


class Health(Message):
    """``GET /healthz`` (unauthenticated): liveness plus the Hub's identity for pairing."""

    status: Literal["ok"]
    hub_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    fingerprint: Annotated[str, Field(pattern=r"^[0-9A-F]{4}(-[0-9A-F]{4}){5}$")] | None
    """Short certificate fingerprint (``make hub-fingerprint``); ``null`` without TLS.
    Advisory: the Display computes the fingerprint from the certificate it was served."""


# --- Pairing ----------------------------------------------------------------------------


class PairRequest(Message):
    """``POST /v1/pair`` (no bearer): the one-time code ``make hub-pair`` showed."""

    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class PairResponse(Message):
    """The shared LAN token, for a Display that entered the right pairing code."""

    token: Annotated[str, Field(min_length=1)]


# --- Home location (#96) ----------------------------------------------------------------

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
PlaceName = Annotated[str, Field(min_length=1, max_length=200)]


def _known_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


TimezoneName = Annotated[str, AfterValidator(_known_timezone)]


class HomeLocation(Message):
    """Where home is, for weather. ``label`` is how to show it (``Portland, Oregon``)."""

    name: PlaceName
    label: str
    lat: Latitude
    lon: Longitude
    timezone: TimezoneName
    country: str | None
    country_code: str | None
    admin1: str | None
    """State or province."""


class LocationSettings(Message):
    """``GET``/``PUT``/``DELETE /v1/settings/location``: the home location in use.

    ``source`` is ``override`` when the Display set it, ``config`` for the config file's
    ``[location]`` (``location`` is ``null`` when that has no coordinates). After a ``PUT`` by
    name, ``alternatives`` lists other matches (most populous first) and ``ambiguous`` says the
    name fitted more than one sizeable place; otherwise ``false`` and ``[]``."""

    source: Literal["config", "override"]
    location: HomeLocation | None
    ambiguous: bool
    alternatives: list[HomeLocation]


class LocationQueryRequest(Message):
    """``PUT /v1/settings/location`` by name: the Hub geocodes it and saves the best match."""

    query: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


class LocationPlaceRequest(Message):
    """``PUT /v1/settings/location`` with a place (an alternative the person picked): saved as
    is. The Display sends every key; the Hub accepts the label fields missing."""

    name: PlaceName
    lat: Latitude
    lon: Longitude
    timezone: TimezoneName
    country: str | None = None
    country_code: str | None = None
    admin1: str | None = None


LocationUpdate = LocationQueryRequest | LocationPlaceRequest


# --- Diagnostics ------------------------------------------------------------------------

CheckStatus = Literal["ok", "warn", "error", "off"]
"""``off``: not configured / disabled. ``warn``: works but needs attention. ``error``: broken,
or the check failed or timed out (``detail`` says which)."""


class UtteranceTrace(Message):
    ts: UtcDatetime
    text: str
    source: Literal["voice", "touch"]
    view: ViewType
    """``context.view`` of the request."""
    intent: str | None
    """``None`` when handling raised (see ``error``)."""
    matched_by: Literal["rules", "qwen", "none"] | None
    say: str | None
    latency_ms: Annotated[int, Field(ge=0)]
    error: str | None


class QwenSummary(Message):
    enabled: bool
    model: str


class SpotifySummary(Message):
    configured: bool
    logged_in: bool


class DiagnosticsSettings(Message):
    timezone: str
    units: Literal["metric", "imperial"]
    location_set: bool
    qwen: QwenSummary
    youtube_configured: bool
    spotify: SpotifySummary
    night: Night
    log_transcripts: bool


class OllamaHealth(Message):
    status: CheckStatus
    reachable: bool
    model_present: bool
    model_loaded: bool
    detail: str | None


class WeatherHealth(Message):
    status: CheckStatus
    fetched_at: UtcDatetime | None
    stale: bool
    detail: str | None


class YoutubeHealth(Message):
    status: CheckStatus
    quota_used: Annotated[int, Field(ge=0)]
    quota_limit: Annotated[int, Field(ge=0)]
    detail: str | None


class SpotifyHealth(Message):
    status: CheckStatus
    device_visible: bool
    device_name: str | None
    detail: str | None


class DiagnosticsHealth(Message):
    ollama: OllamaHealth
    weather: WeatherHealth
    youtube: YoutubeHealth
    spotify: SpotifyHealth


class Diagnostics(Message):
    server_time: UtcDatetime
    version: str
    git_sha: str | None
    started_at: UtcDatetime
    uptime_s: Seconds
    settings: DiagnosticsSettings
    health: DiagnosticsHealth
    utterances: list[UtteranceTrace]
    """The most recent utterances (up to 20), newest first."""
