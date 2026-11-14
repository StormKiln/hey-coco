from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.intents.engine import no_fallback
from coco_hub.protocol import Action
from coco_hub.qwen.fallback import QwenFallback

from conftest import TEST_TOKEN
from fake_ollama import FakeOllama, down_transport

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
WARM_UP = {"intent": "time.now", "slots": {}, "answer": None}


def body(text: str) -> dict:
    ctx = {"view": "idle", "alarm_ringing": False, "media_playing": None}
    return {"text": text, "source": "voice", "context": ctx}


async def wait_for_warm_up(app) -> None:
    await app.state.qwen._warming


def enabled(settings):
    return settings.model_copy(update={"qwen": settings.qwen.model_copy(update={"enabled": True})})


def test_disabled_by_default_uses_no_fallback(settings):
    fake = FakeOllama()
    app = create_app(settings, ollama_transport=fake.transport)
    assert app.state.fallback is no_fallback
    assert app.state.qwen is None
    with TestClient(app) as client:
        response = client.post("/v1/utterance", json=body("why is the sky blue"), headers=AUTH)
    assert Action.model_validate(response.json()).matched_by == "none"
    assert fake.chats == []


def test_enabled_installs_qwen_and_answers(settings):
    sky = "Sunlight scatters off air, and blue scatters the most."
    fake = FakeOllama(WARM_UP, {"intent": "general.answer", "slots": {}, "answer": sky})
    app = create_app(enabled(settings), ollama_transport=fake.transport)
    assert isinstance(app.state.fallback, QwenFallback)
    with TestClient(app) as client:
        client.portal.call(wait_for_warm_up, app)
        response = client.post("/v1/utterance", json=body("why is the sky blue"), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "say": sky,
        "view": {"type": "answer", "text": sky},
        "commands": [],
        "intent": "general.answer",
        "matched_by": "qwen",
    }
    assert len(fake.chats) == 2  # warm-up, then the question


def test_rules_still_win_when_qwen_enabled(settings):
    fake = FakeOllama(WARM_UP)
    app = create_app(enabled(settings), ollama_transport=fake.transport)
    with TestClient(app) as client:
        client.portal.call(wait_for_warm_up, app)
        response = client.post("/v1/utterance", json=body("how do you spell cat"), headers=AUTH)
    assert response.json()["matched_by"] == "rules"
    assert len(fake.chats) == 1  # only the warm-up


def test_hub_runs_normally_when_ollama_is_down(settings):
    app = create_app(enabled(settings), ollama_transport=down_transport())
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.post("/v1/utterance", json=body("why is the sky blue"), headers=AUTH)
        rules = client.post("/v1/utterance", json=body("what time is it"), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["say"] == "Sorry, I can't do that yet."
    assert response.json()["matched_by"] == "none"
    assert rules.json()["matched_by"] == "rules"
