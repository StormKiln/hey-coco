"""Pictures and sounds through the real app: wiring, utterance route, and media URLs."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.pictures.commons import CommonsClient, PictureService
from coco_hub.protocol import Action, PictureView, SoundView

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
COMMONS = Path(__file__).parent / "fixtures" / "commons"


def body(text: str, view: str = "idle") -> dict:
    return {
        "text": text,
        "source": "voice",
        "context": {"view": view, "alarm_ringing": False, "media_playing": None},
    }


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_app_has_a_picture_service(app):
    assert isinstance(app.state.services["pictures"], PictureService)


def test_sound_utterance_and_its_media_are_served(client):
    response = client.post("/v1/utterance", json=body("what does a cow say"), headers=AUTH)
    assert response.status_code == 200
    action = Action.model_validate(response.json())
    assert action.intent == "sound.play" and action.say == "A cow says moo!"
    assert isinstance(action.view, SoundView)
    # The Hub-relative paths resolve against the Hub base URL, with the bearer token.
    clip = client.get(action.view.clip_url, headers=AUTH)
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mp4"
    image = client.get(action.view.image_url, headers=AUTH)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get(action.view.clip_url).status_code == 401

    again = client.post("/v1/utterance", json=body("again", view="sound"), headers=AUTH)
    assert Action.model_validate(again.json()).view == action.view


def test_picture_utterance_then_next(app, client):
    recorded = json.loads((COMMONS / "fire_truck.json").read_text())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=recorded))
    app.state.services["pictures"] = PictureService(CommonsClient(transport=transport))

    response = client.post("/v1/utterance", json=body("show me a fire truck"), headers=AUTH)
    action = Action.model_validate(response.json())
    assert action.intent == "picture.show" and action.say == "Here's a fire truck."
    assert isinstance(action.view, PictureView) and len(action.view.images) == 8

    nxt = client.post("/v1/utterance", json=body("next one", view="picture"), headers=AUTH)
    nxt_action = Action.model_validate(nxt.json())
    assert isinstance(nxt_action.view, PictureView)
    assert nxt_action.view.images[0] == action.view.images[1]
