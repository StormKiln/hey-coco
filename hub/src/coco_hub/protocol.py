"""Hub <-> Display protocol models (v1).

The contract is `docs/protocol/README.md`; every fixture in `docs/protocol/fixtures/` must
validate against these models and round-trip unchanged (`tests/test_protocol_contract.py`).
Change the README, the fixtures, these models and the Swift models together.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
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


class WeatherView(Message):
    type: Literal["weather"] = "weather"
    current: WeatherNow
    hourly: list[WeatherHour]
    daily: list[WeatherDay]


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


Command = Annotated[
    AlarmStop | MediaCommand | VolumeSet | VolumeChange | ScreenWake | ScreenSleep,
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


Event = Annotated[
    HelloEvent | TimerEvent | NowPlayingEvent | SettingsChangedEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


# --- Errors -----------------------------------------------------------------------------


class ErrorBody(Message):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    message: str


class ErrorResponse(Message):
    error: ErrorBody
