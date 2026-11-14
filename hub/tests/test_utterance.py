import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.intents.engine import Intent, Reply
from coco_hub.protocol import Action

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
FIXED = datetime(2026, 11, 14, 20, 5, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "protocol" / "fixtures"


def body(text: str, **context) -> dict:
    ctx = {"view": "idle", "alarm_ringing": False, "media_playing": None} | context
    return {"text": text, "source": "voice", "context": ctx}


@pytest.fixture
def app(settings):
    toronto = settings.model_copy(update={"timezone": "America/Toronto"})
    return create_app(toronto, clock=lambda: FIXED)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TEST_TOKEN}]
)
def test_utterance_requires_token(client, headers):
    response = client.post("/v1/utterance", json=body("what time is it"), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_time_now(client):
    response = client.post("/v1/utterance", json=body("Hey Coco, what time is it?"), headers=AUTH)
    assert response.status_code == 200
    action = Action.model_validate(response.json())
    assert action == Action(
        say="It's 3:05 PM.",
        view={"type": "answer", "text": "It's 3:05 PM."},
        commands=[],
        intent="time.now",
        matched_by="rules",
    )


def test_date_today(client):
    response = client.post("/v1/utterance", json=body("what's the date"), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "say": "It's Saturday, November 14th.",
        "view": {"type": "answer", "text": "It's Saturday, November 14th."},
        "commands": [],
        "intent": "date.today",
        "matched_by": "rules",
    }


def test_no_match_says_sorry(client):
    response = client.post("/v1/utterance", json=body("sing me a song"), headers=AUTH)
    assert response.status_code == 200
    Action.model_validate(response.json())
    assert response.json() == json.loads((FIXTURES / "action_none.json").read_text())


def test_invalid_request_rejected(client):
    response = client.post("/v1/utterance", json={"text": "hi"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_engine_lives_on_app_state_for_later_modules(app):
    app.state.intents.register(
        Intent("demo.ping", patterns=["ping"], handler=lambda m, r, c: Reply(say="Pong."))
    )
    with TestClient(app) as c:
        response = c.post("/v1/utterance", json=body("ping"), headers=AUTH)
    assert response.json()["say"] == "Pong."
    assert response.json()["intent"] == "demo.ping"


def test_fallback_is_pluggable(app):
    async def fallback(no_match, request, ctx):
        return Action(
            say=f"You said {no_match.text}.",
            view=None,
            commands=[],
            intent="general.answer",
            matched_by="qwen",
        )

    app.state.fallback = fallback
    with TestClient(app) as c:
        response = c.post("/v1/utterance", json=body("Hey Coco, Sing!"), headers=AUTH)
    assert response.json()["say"] == "You said sing."
    assert response.json()["matched_by"] == "qwen"


def test_services_reach_handlers(app):
    app.state.services["greeting"] = "Howdy."
    app.state.intents.register(
        Intent(
            "demo.greet",
            patterns=["greet me"],
            handler=lambda m, r, c: Reply(say=c.services["greeting"]),
        )
    )
    with TestClient(app) as c:
        response = c.post("/v1/utterance", json=body("greet me"), headers=AUTH)
    assert response.json()["say"] == "Howdy."
