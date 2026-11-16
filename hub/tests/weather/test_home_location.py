"""#96: the home (default) weather location, set from the Display's Settings.

A place stored in the Hub's database overrides ``[location]`` in the config file. Geocoding is
served from the recorded Open-Meteo responses (``conftest.FakeOpenMeteo``); nothing here
touches the network.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from coco_hub import db, home
from coco_hub.app import create_app
from coco_hub.protocol import LocationSettings
from coco_hub.weather.places import Place, choose, parse_results
from coco_hub.weather.service import WeatherService

from .conftest import FakeOpenMeteo, load
from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
URL = "/v1/settings/location"

MAINE = Place(
    name="Portland",
    lat=43.65737,
    lon=-70.2589,
    timezone="America/New_York",
    country="United States",
    country_code="US",
    admin1="Maine",
)


# --- alternatives -----------------------------------------------------------------------


def test_a_pick_lists_the_other_matches_biggest_first():
    pick = choose(parse_results(load("geocode_portland")), qualifier=None)
    assert pick is not None
    assert pick.place.label == "Portland, Oregon"
    labels = [p.label for p in pick.alternatives]
    assert labels == [
        "Portland, Maine",
        "Portland, Texas",
        "Portland, United Kingdom",
        "Portland, Tennessee",
        "Portland, Indiana",
    ]


def test_a_qualified_pick_still_lists_alternatives():
    pick = choose(parse_results(load("geocode_portland")), qualifier="maine")
    assert pick is not None
    assert pick.place.label == "Portland, Maine"
    assert pick.place not in pick.alternatives
    assert pick.alternatives[0].label == "Portland, Oregon"


# --- storage ----------------------------------------------------------------------------


def test_store_round_trips_and_clears(tmp_path):
    conn = db.open_db(tmp_path)
    try:
        assert home.load(conn) is None
        home.save(conn, MAINE)
        assert home.load(conn) == MAINE
        home.save(conn, replace(MAINE, name="Portland ME"))
        loaded = home.load(conn)
        assert loaded is not None and loaded.name == "Portland ME"
        home.clear(conn)
        assert home.load(conn) is None
    finally:
        conn.close()


# --- the weather service ----------------------------------------------------------------


@pytest.mark.anyio
async def test_override_sets_the_location_without_a_config_one(
    settings, make_service, fake_open_meteo
):
    service = make_service(settings)
    assert not service.configured
    service.set_home(MAINE)
    assert service.configured
    assert await service.get() is not None
    params = fake_open_meteo.forecasts[-1].url.params
    assert (params["latitude"], params["longitude"]) == ("43.65737", "-70.2589")
    await service.aclose()


@pytest.mark.anyio
async def test_changing_home_drops_the_cached_forecast(make_service, fake_open_meteo):
    service = make_service()
    await service.get()
    service.set_home(MAINE)
    await service.get()
    assert len(fake_open_meteo.forecasts) == 2
    assert fake_open_meteo.forecasts[-1].url.params["latitude"] == "43.65737"
    service.set_home(None)  # back to the config file's location
    await service.get()
    assert len(fake_open_meteo.forecasts) == 3
    assert fake_open_meteo.forecasts[-1].url.params["latitude"] == "45.42"
    await service.aclose()


@pytest.mark.anyio
async def test_a_fetch_for_the_old_home_is_not_kept(make_service, fake_open_meteo):
    service = make_service()
    fake_open_meteo.hang = True
    pending = service._start_fetch()
    service.set_home(MAINE)
    fake_open_meteo.release()
    await pending
    assert service.snapshot is None
    await service.aclose()


# --- the API ----------------------------------------------------------------------------


@pytest.fixture
def make_client(clock):
    """``with make_client(settings) as (client, fake):`` a Hub whose weather and geocoding
    are the recordings."""

    def make(settings, fake: FakeOpenMeteo | None = None):
        fake = fake or FakeOpenMeteo()
        service = WeatherService(settings, transport=fake.transport, clock=clock)
        app = create_app(settings, clock=clock, weather=service)

        class _Ctx:
            def __enter__(self):
                self.client = TestClient(app)
                self.client.__enter__()
                return self.client, fake

            def __exit__(self, *exc):
                return self.client.__exit__(*exc)

        return _Ctx()

    return make


def location(client) -> LocationSettings:
    response = client.get(URL, headers=AUTH)
    assert response.status_code == 200
    return checked(response)


def checked(response) -> LocationSettings:
    value = LocationSettings.model_validate(response.json())
    assert value.model_dump(mode="json") == response.json()
    return value


def test_get_reports_the_config_location(home, make_client):
    named = home.model_copy(update={"location": home.location.model_copy(update={"name": "Home"})})
    with make_client(named) as (client, _):
        value = location(client)
    assert value.source == "config"
    assert value.location is not None
    assert (value.location.lat, value.location.lon) == (45.42, -75.69)
    assert value.location.name == value.location.label == "Home"
    assert value.location.timezone == "America/Toronto"
    assert value.location.country is None
    assert not value.ambiguous
    assert value.alternatives == []


def test_get_without_any_location(settings, make_client):
    with make_client(settings) as (client, _):
        value = location(client)
    assert value.source == "config"
    assert value.location is None


def test_put_a_name_geocodes_saves_and_offers_alternatives(home, make_client):
    with make_client(home) as (client, fake):
        response = client.put(URL, json={"query": "Portland"}, headers=AUTH)
        assert response.status_code == 200
        value = checked(response)
        assert fake.searches == ["Portland"]
        assert value.source == "override"
        assert value.location is not None
        assert value.location.label == "Portland, Oregon"
        assert value.location.timezone == "America/Los_Angeles"
        assert (value.location.country_code, value.location.admin1) == ("US", "Oregon")
        assert value.ambiguous
        assert [a.label for a in value.alternatives][:2] == ["Portland, Maine", "Portland, Texas"]
        # Saved: GET says so, without alternatives.
        again = location(client)
        assert again.source == "override"
        assert again.location == value.location
        assert again.alternatives == []
        # The idle screen's weather is now for Portland.
        assert client.get("/v1/idle", headers=AUTH).status_code == 200
        assert fake.forecasts[-1].url.params["latitude"] == "45.52345"


def test_put_a_qualified_name(home, make_client):
    with make_client(home) as (client, _):
        value = checked(client.put(URL, json={"query": "Portland, Maine"}, headers=AUTH))
    assert value.location is not None
    assert value.location.label == "Portland, Maine"
    assert not value.ambiguous


def test_put_a_picked_alternative(home, make_client):
    body = {
        "name": "Portland",
        "lat": 43.65737,
        "lon": -70.2589,
        "timezone": "America/New_York",
        "country": "United States",
        "country_code": "US",
        "admin1": "Maine",
    }
    with make_client(home) as (client, fake):
        value = checked(client.put(URL, json=body, headers=AUTH))
        assert fake.searches == []  # nothing to geocode
        assert value.source == "override"
        assert value.location is not None
        assert value.location.label == "Portland, Maine"
        assert location(client).location == value.location


def test_put_a_place_with_only_the_required_keys(home, make_client):
    body = {"name": "Cabin", "lat": 46.0, "lon": -77.0, "timezone": "America/Toronto"}
    with make_client(home) as (client, _):
        value = checked(client.put(URL, json=body, headers=AUTH))
    assert value.location is not None
    assert value.location.label == "Cabin"
    assert value.location.country is None


@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "Portland", "lat": 1.0},
        {"name": "X", "lat": 91.0, "lon": 0.0, "timezone": "UTC"},
        {"name": "X", "lat": 0.0, "lon": 0.0, "timezone": "Mars/Olympus"},
        {"name": "", "lat": 0.0, "lon": 0.0, "timezone": "UTC"},
        {},
    ],
)
def test_put_rejects_bad_requests(home, make_client, body):
    with make_client(home) as (client, _):
        response = client.put(URL, json=body, headers=AUTH)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert location(client).source == "config"


def test_put_a_name_that_matches_nothing(home, make_client):
    with make_client(home) as (client, _):
        response = client.put(URL, json={"query": "Narnia"}, headers=AUTH)
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "location_not_found"
        assert "Narnia" in error["message"]
        assert location(client).source == "config"


def test_put_while_geocoding_is_unreachable(home, make_client):
    fake = FakeOpenMeteo()
    fake.fail = True
    with make_client(home, fake) as (client, _):
        response = client.put(URL, json={"query": "Portland"}, headers=AUTH)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "geocoding_unavailable"


def test_delete_goes_back_to_the_config_location(home, make_client):
    with make_client(home) as (client, fake):
        client.put(URL, json={"query": "Portland"}, headers=AUTH)
        response = client.delete(URL, headers=AUTH)
        assert response.status_code == 200
        value = checked(response)
        assert value.source == "config"
        assert value.location is not None and value.location.lat == 45.42
        assert location(client).source == "config"
        client.get("/v1/idle", headers=AUTH)
        assert fake.forecasts[-1].url.params["latitude"] == "45.42"


def test_the_override_survives_a_restart(home, make_client):
    with make_client(home) as (client, _):
        client.put(URL, json={"query": "Portland, Maine"}, headers=AUTH)
    with make_client(home) as (client, fake):
        value = location(client)
        assert value.source == "override"
        assert value.location is not None and value.location.label == "Portland, Maine"
        client.get("/v1/idle", headers=AUTH)
        assert fake.forecasts[-1].url.params["latitude"] == "43.65737"


def test_diagnostics_counts_the_override_as_a_location(settings, make_client):
    with make_client(settings) as (client, _):
        assert location_set(client) is False
        client.put(URL, json={"query": "Portland"}, headers=AUTH)
        assert location_set(client) is True


def location_set(client) -> bool:
    return client.get("/v1/diagnostics", headers=AUTH).json()["settings"]["location_set"]
