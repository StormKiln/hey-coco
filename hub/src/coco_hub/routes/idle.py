"""``GET /v1/idle``: what the idle screen shows (clock, weather, night settings)."""

from fastapi import APIRouter, Request

from coco_hub.protocol import IdleInfo, IdleWeather, Night
from coco_hub.weather.service import WeatherService

router = APIRouter()

IDLE_HOURS = 12
WEATHER_WAIT_S = 0.5
"""The idle screen never waits on the internet longer than this; it shows the cached
forecast (or none) and picks up the new one on its next poll."""


@router.get("/idle", response_model=IdleInfo)
async def idle(request: Request) -> IdleInfo:
    state = request.app.state
    settings = state.settings
    now = state.clock()
    weather: WeatherService | None = state.services.get("weather")
    snapshot = await weather.get(max_wait=WEATHER_WAIT_S) if weather is not None else None
    return IdleInfo(
        server_time=now,
        timezone=settings.timezone,
        weather=None
        if snapshot is None
        else IdleWeather(
            current=snapshot.forecast.current,
            hourly=snapshot.forecast.hourly_after(now, IDLE_HOURS),
            stale=snapshot.stale,
        ),
        night=Night(
            start=settings.night.start.strftime("%H:%M"),
            end=settings.night.end.strftime("%H:%M"),
            dark_after_idle_s=settings.night.dark_after_idle_s,
        ),
    )
