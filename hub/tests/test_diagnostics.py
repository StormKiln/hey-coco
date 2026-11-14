import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from coco_hub.app import create_app
from coco_hub.diagnostics import TraceBuffer
from coco_hub.intents.engine import Intent
from coco_hub.protocol import Diagnostics, UtteranceTrace

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
FIXED = datetime(2026, 11, 14, 20, 5, tzinfo=UTC)
YT_KEY = "AIzaSyFakeYoutubeKey0123456789"
SPOTIFY_SECRET = "spotify-client-secret-abcdef"


def body(text: str, view: str = "idle", source: str = "voice") -> dict:
    ctx = {"view": view, "alarm_ringing": False, "media_playing": None}
    return {"text": text, "source": source, "context": ctx}


def trace(n: int) -> UtteranceTrace:
    return UtteranceTrace(
        ts=FIXED,
        text=f"utterance {n}",
        source="voice",
        view="idle",
        intent="time.now",
        matched_by="rules",
        say="ok",
        latency_ms=1,
        error=None,
    )


@pytest.fixture
def app(settings):
    return create_app(settings, clock=lambda: FIXED)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- ring buffer --------------------------------------------------------------------------


def test_ring_buffer_is_bounded_and_newest_first():
    buffer = TraceBuffer(maxlen=100)
    for n in range(250):
        buffer.append(trace(n))
    assert len(buffer) == 100
    recent = buffer.recent(20)
    assert [t.text for t in recent] == [f"utterance {n}" for n in range(249, 229, -1)]
    assert buffer.recent(500)[-1].text == "utterance 150"


# --- utterance traces ---------------------------------------------------------------------


def test_trace_recorded_for_rules_match(client, app):
    client.post("/v1/utterance", json=body("what time is it", view="timers"), headers=AUTH)
    [t] = app.state.diagnostics.traces.recent(10)
    assert t.text == "what time is it"
    assert t.source == "voice"
    assert t.view == "timers"
    assert t.intent == "time.now"
    assert t.matched_by == "rules"
    assert t.say and t.say.startswith("It's")
    assert t.latency_ms >= 0
    assert t.error is None
    assert t.ts == FIXED


def test_trace_recorded_for_no_match(client, app):
    client.post("/v1/utterance", json=body("flibbertigibbet", source="touch"), headers=AUTH)
    [t] = app.state.diagnostics.traces.recent(10)
    assert (t.intent, t.matched_by, t.source, t.error) == ("none", "none", "touch", None)


def test_trace_recorded_when_handler_raises(client, app):
    def explode(match, request, ctx):
        raise RuntimeError("kaboom")

    app.state.intents.register(Intent("test.explode", [r"explode"], explode, priority=100))
    response = client.post("/v1/utterance", json=body("explode"), headers=AUTH)
    assert response.status_code == 500
    [t] = app.state.diagnostics.traces.recent(10)
    assert t.text == "explode"
    assert t.intent is None and t.matched_by is None and t.say is None
    assert t.error == "RuntimeError: kaboom"


def test_trace_logged_at_info_with_transcript(client, caplog):
    with caplog.at_level(logging.INFO, logger="coco_hub.utterance"):
        client.post("/v1/utterance", json=body("what time is it"), headers=AUTH)
    [rec] = [r for r in caplog.records if r.name == "coco_hub.utterance"]
    assert rec.levelno == logging.INFO
    assert rec.trace["text"] == "what time is it"
    assert rec.trace["intent"] == "time.now"
    assert "what time is it" in rec.getMessage()


def test_log_transcripts_false_drops_text_and_say_from_logs(settings, caplog):
    quiet = settings.model_copy(
        update={"diagnostics": settings.diagnostics.model_copy(update={"log_transcripts": False})}
    )
    app = create_app(quiet, clock=lambda: FIXED)
    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="coco_hub.utterance"):
        client.post("/v1/utterance", json=body("what time is it"), headers=AUTH)
        [rec] = [r for r in caplog.records if r.name == "coco_hub.utterance"]
        assert "text" not in rec.trace and "say" not in rec.trace
        assert rec.trace["intent"] == "time.now"
        assert "what time is it" not in rec.getMessage()
        assert "It's" not in rec.getMessage()
        # Still kept in memory for the diagnostics screen.
        assert app.state.diagnostics.traces.recent(1)[0].text == "what time is it"


# --- GET /v1/diagnostics ------------------------------------------------------------------


def test_diagnostics_requires_token(client):
    assert client.get("/v1/diagnostics").status_code == 401


def test_diagnostics_shape(client):
    for n in range(25):
        client.post("/v1/utterance", json=body(f"what time is it {n}"), headers=AUTH)
    response = client.get("/v1/diagnostics", headers=AUTH)
    assert response.status_code == 200
    diag = Diagnostics.model_validate(response.json())
    assert diag.version
    assert diag.server_time == FIXED
    assert diag.uptime_s >= 0
    assert diag.settings.location_set is False
    assert diag.settings.qwen.enabled is False
    assert diag.settings.night.start == "21:30"
    assert diag.health.ollama.status == "off"
    assert diag.health.weather.status == "off"
    assert diag.health.youtube.status == "off"
    assert diag.health.spotify.status == "off"
    assert len(diag.utterances) == 20
    assert diag.utterances[0].text == "what time is it 24"


def test_diagnostics_never_contains_secrets(settings):
    loaded = settings.model_copy(
        update={
            "keys": settings.keys.model_copy(update={"youtube": SecretStr(YT_KEY)}),
            "spotify": settings.spotify.model_copy(
                update={"client_id": "cid", "client_secret": SecretStr(SPOTIFY_SECRET)}
            ),
        }
    )
    app = create_app(loaded, clock=lambda: FIXED)
    with TestClient(app) as client:
        client.post("/v1/utterance", json=body("what time is it"), headers=AUTH)
        text = client.get("/v1/diagnostics", headers=AUTH).text
    for secret in (TEST_TOKEN, YT_KEY, SPOTIFY_SECRET):
        assert secret not in text
    data = json.loads(text)
    assert data["settings"]["youtube_configured"] is True
    assert data["settings"]["spotify"] == {"configured": True, "logged_in": False}
    assert data["health"]["spotify"]["status"] == "warn"


class SlowSpotify:
    configured = True
    logged_in = True
    device_name = "Kitchen iPad"

    async def visible_device(self):
        await asyncio.sleep(30)


class BrokenYoutube:
    configured = True
    daily_budget = 9000

    def quota_used(self) -> int:
        raise RuntimeError("database is locked")


def test_slow_or_broken_checks_do_not_fail_the_endpoint(client, app):
    app.state.diagnostics.check_timeout_s = 0.05
    app.state.services["spotify"] = SlowSpotify()
    app.state.services["youtube"] = BrokenYoutube()
    response = client.get("/v1/diagnostics", headers=AUTH)
    assert response.status_code == 200
    health = response.json()["health"]
    assert health["spotify"]["status"] == "error"
    assert health["spotify"]["detail"] == "timed out"
    assert health["youtube"]["status"] == "error"
    assert health["youtube"]["detail"] == "RuntimeError"


def ollama_transport(*, up: bool) -> httpx.MockTransport:
    models = {"models": [{"name": "qwen3:8b"}]}

    def handle(request: httpx.Request) -> httpx.Response:
        if not up:
            raise httpx.ConnectError("Connection refused", request=request)
        if request.url.path in ("/api/tags", "/api/ps"):
            return httpx.Response(200, json=models)
        # Warm-up chat: an empty reply is fine (the fallback only logs failures).
        return httpx.Response(500, json={"error": "not in this test"})

    return httpx.MockTransport(handle)


def test_ollama_down_reports_error(settings):
    enabled = settings.model_copy(
        update={"qwen": settings.qwen.model_copy(update={"enabled": True})}
    )
    app = create_app(enabled, clock=lambda: FIXED, ollama_transport=ollama_transport(up=False))
    with TestClient(app) as client:
        data = client.get("/v1/diagnostics", headers=AUTH).json()
    assert data["settings"]["qwen"] == {"enabled": True, "model": "qwen3:8b"}
    assert data["health"]["ollama"]["status"] == "error"
    assert data["health"]["ollama"]["reachable"] is False


def test_ollama_ready_reports_ok(settings):
    enabled = settings.model_copy(
        update={"qwen": settings.qwen.model_copy(update={"enabled": True})}
    )
    app = create_app(enabled, clock=lambda: FIXED, ollama_transport=ollama_transport(up=True))
    with TestClient(app) as client:
        data = client.get("/v1/diagnostics", headers=AUTH).json()
    ollama = data["health"]["ollama"]
    assert ollama["status"] == "ok"
    assert ollama["model_loaded"] is True
