"""The internet or Ollama is down: the clock, timers and rules keep working, fast.

Two kinds of "down" for each upstream:
- refused: nothing listens (the service is off, or DNS/route fails fast);
- black hole: the TCP connection opens but nothing ever answers (a dead upstream behind a
  working LAN, or a captive Wi-Fi). Requests hang until a timeout.
"""

import time

import pytest

from coco_hub.intents.engine import UNMATCHED_SAY
from coco_hub.intents.weather import UNREACHABLE
from coco_hub.weather.service import GET_MAX_WAIT_S

pytestmark = pytest.mark.resilience

FAST_S = 1.0
UNMATCHED = "blorp the flibbertigibbet"


def timed(fn, *args, **kwargs):
    started = time.monotonic()
    result = fn(*args, **kwargs)
    return result, time.monotonic() - started


def assert_core_works(hub) -> None:
    """Clock, voice timers and the idle screen answer quickly with no internet."""
    response, elapsed = timed(hub.get, "/v1/idle")
    assert response.status_code == 200, response.text
    assert elapsed < FAST_S, f"/v1/idle took {elapsed:.2f}s"
    assert response.json()["weather"] is None  # never fetched: nothing to show
    assert response.json()["server_time"]

    action, elapsed = timed(hub.say, "what time is it")
    assert action["intent"] == "time.now"
    assert action["say"].startswith("It's")
    assert elapsed < FAST_S

    action, elapsed = timed(hub.say, "set a timer for 1 minute")
    assert action["intent"] == "timer.create"
    assert elapsed < FAST_S
    assert [t["duration_s"] for t in hub.timers()] == [60]


# --- 2. Internet down -------------------------------------------------------------------


def test_internet_refused_core_keeps_working(hub):
    assert_core_works(hub)
    action, elapsed = timed(hub.say, "what's the weather")
    assert action["intent"] == "weather.current"
    assert action["say"] == UNREACHABLE
    assert elapsed < FAST_S


def test_internet_black_hole_core_keeps_working(make_hub, black_hole):
    hub = make_hub(weather_url=f"http://127.0.0.1:{black_hole.port}/v1/forecast")
    hub.start()  # the background refresh is now stuck waiting on the black hole
    assert_core_works(hub)
    # A weather question waits a little longer for the network than the idle screen does.
    action, elapsed = timed(hub.say, "what's the weather")
    assert action["intent"] == "weather.current"
    assert action["say"] == UNREACHABLE
    assert elapsed < GET_MAX_WAIT_S + FAST_S


def test_internet_down_diagnostics_reports_weather(hub):
    hub.get("/v1/idle")  # makes the (failing) fetch happen
    weather = hub.get("/v1/diagnostics").json()["health"]["weather"]
    assert weather["status"] == "error"
    assert weather["detail"] == "last fetch failed"


# --- 3. Ollama down ---------------------------------------------------------------------


def test_ollama_refused_unmatched_says_sorry_fast_and_rules_still_work(hub):
    action, elapsed = timed(hub.say, UNMATCHED)
    assert action["say"] == UNMATCHED_SAY
    assert elapsed < FAST_S

    action, elapsed = timed(hub.say, "set a timer for 2 minutes")
    assert (action["intent"], action["matched_by"]) == ("timer.create", "rules")
    assert elapsed < FAST_S

    ollama = hub.get("/v1/diagnostics").json()["health"]["ollama"]
    assert (ollama["status"], ollama["reachable"]) == ("error", False)


def test_ollama_black_hole_gives_up_within_its_deadline(make_hub, black_hole):
    hub = make_hub(ollama_url=f"http://127.0.0.1:{black_hole.port}")
    hub.start()
    action, elapsed = timed(hub.say, UNMATCHED, timeout=15)
    assert action["say"] == "Sorry, that took too long."
    assert elapsed < 6.0 + FAST_S  # qwen.timeout_s, plus slack

    action, elapsed = timed(hub.say, "set a timer for 2 minutes")
    assert action["matched_by"] == "rules"
    assert elapsed < FAST_S

    diag, elapsed = timed(hub.get, "/v1/diagnostics")
    assert diag.json()["health"]["ollama"]["status"] == "error"
    assert elapsed < 2 * FAST_S  # health checks are bounded at 1.5 s
