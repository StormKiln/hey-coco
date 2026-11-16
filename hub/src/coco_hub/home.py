"""The home location set from the Display's Settings (#96).

One row in ``home_location``. When present it overrides ``[location]`` in the config file
(``WeatherService.set_home``); deleting it goes back to the config file's location.
"""

import sqlite3

from coco_hub.weather.places import Place


def load(conn: sqlite3.Connection) -> Place | None:
    row = conn.execute(
        "SELECT name, lat, lon, timezone, country, country_code, admin1"
        " FROM home_location WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    name, lat, lon, timezone, country, country_code, admin1 = row
    return Place(
        name=name,
        lat=lat,
        lon=lon,
        timezone=timezone,
        country=country,
        country_code=country_code,
        admin1=admin1,
    )


def save(conn: sqlite3.Connection, place: Place) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO home_location"
            " (id, name, lat, lon, timezone, country, country_code, admin1)"
            " VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (
                place.name,
                place.lat,
                place.lon,
                place.timezone,
                place.country,
                place.country_code,
                place.admin1,
            ),
        )


def clear(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM home_location")
