"""``/v1/settings/location``: the home weather location, set from the Display (#96).

``PUT`` by name geocodes it (``weather.places``, the same geocoder as "weather in Portland"),
saves the best match and lists the other matches so the Display can offer them; ``PUT`` with a
place saves it as is (the person picked an alternative). ``DELETE`` goes back to the config
file's ``[location]``. The stored place overrides the config file's until then.
"""

import logging

from fastapi import APIRouter, Request, status

from coco_hub import home
from coco_hub.errors import HubError
from coco_hub.protocol import (
    HomeLocation,
    LocationQueryRequest,
    LocationSettings,
    LocationUpdate,
)
from coco_hub.settings import Settings
from coco_hub.weather.client import WeatherFetchError
from coco_hub.weather.places import Place
from coco_hub.weather.service import WeatherService

router = APIRouter()
log = logging.getLogger(__name__)

DEFAULT_NAME = "Home"


def _from_place(place: Place) -> HomeLocation:
    return HomeLocation(
        name=place.name,
        label=place.label,
        lat=place.lat,
        lon=place.lon,
        timezone=place.timezone,
        country=place.country or None,
        country_code=place.country_code or None,
        admin1=place.admin1 or None,
    )


def _from_config(settings: Settings) -> HomeLocation | None:
    loc = settings.location
    if loc.lat is None or loc.lon is None:
        return None
    name = loc.name.strip() or DEFAULT_NAME
    return HomeLocation(
        name=name,
        label=name,
        lat=loc.lat,
        lon=loc.lon,
        timezone=settings.timezone,
        country=None,
        country_code=None,
        admin1=None,
    )


def _current(request: Request, weather: WeatherService) -> LocationSettings:
    place = weather.home
    if place is not None:
        return LocationSettings(
            source="override", location=_from_place(place), ambiguous=False, alternatives=[]
        )
    return LocationSettings(
        source="config",
        location=_from_config(request.app.state.settings),
        ambiguous=False,
        alternatives=[],
    )


def _weather(request: Request) -> WeatherService:
    return request.app.state.services["weather"]


@router.get("/settings/location", response_model=LocationSettings)
async def get_location(request: Request) -> LocationSettings:
    return _current(request, _weather(request))


@router.put("/settings/location", response_model=LocationSettings)
async def put_location(body: LocationUpdate, request: Request) -> LocationSettings:
    weather = _weather(request)
    ambiguous = False
    alternatives: list[HomeLocation] = []
    if isinstance(body, LocationQueryRequest):
        query = body.query.strip()
        try:
            pick = await weather.resolve(query)
        except WeatherFetchError as exc:
            log.warning("home location lookup failed: %s", exc)
            raise HubError(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "geocoding_unavailable",
                "Coco Hub can't look up places right now. Check its internet connection.",
            ) from exc
        if pick is None:
            raise HubError(
                status.HTTP_404_NOT_FOUND,
                "location_not_found",
                f'Coco couldn\'t find "{query}". Try adding the state or country, '
                'like "Portland, Oregon".',
            )
        place = pick.place
        ambiguous = pick.ambiguous
        alternatives = [_from_place(p) for p in pick.alternatives]
    else:
        place = Place(
            name=body.name.strip() or DEFAULT_NAME,
            lat=body.lat,
            lon=body.lon,
            timezone=body.timezone,
            country=body.country or "",
            country_code=body.country_code or "",
            admin1=body.admin1 or "",
        )
    home.save(request.app.state.db, place)
    weather.set_home(place)
    log.info("Home location set to %s", place.label)
    return LocationSettings(
        source="override",
        location=_from_place(place),
        ambiguous=ambiguous,
        alternatives=alternatives,
    )


@router.delete("/settings/location", response_model=LocationSettings)
async def delete_location(request: Request) -> LocationSettings:
    weather = _weather(request)
    home.clear(request.app.state.db)
    weather.set_home(None)
    log.info("Home location back to the config file's")
    return _current(request, weather)
