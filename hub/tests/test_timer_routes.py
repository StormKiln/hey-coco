from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coco_hub.app import create_app
from coco_hub.protocol import EVENT_ADAPTER, Action, Timer, TimersList

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
T0 = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(settings, clock):
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def ws_connect(client: TestClient):
    # Send both forms of the token so this test doesn't depend on how auth reads it.
    return client.websocket_connect(f"/v1/events?token={TEST_TOKEN}", headers=AUTH)


def utter(client, text: str, *, ringing: bool = False) -> dict:
    ctx = {"view": "idle", "alarm_ringing": ringing, "media_playing": None}
    response = client.post(
        "/v1/utterance", json={"text": text, "source": "voice", "context": ctx}, headers=AUTH
    )
    assert response.status_code == 200
    Action.model_validate(response.json())
    return response.json()


# --- REST --------------------------------------------------------------------------------


def test_timers_require_token(client):
    assert client.get("/v1/timers").status_code == 401


def test_create_list_dismiss_delete(client, app, clock):
    response = client.post("/v1/timers", json={"name": "Eggs", "duration_s": 420}, headers=AUTH)
    assert response.status_code == 200
    eggs = Timer.model_validate(response.json())
    assert (eggs.name, eggs.duration_s, eggs.state) == ("eggs", 420, "running")
    assert eggs.ends_at == T0 + timedelta(seconds=420)

    unnamed = client.post("/v1/timers", json={"name": None, "duration_s": 60}, headers=AUTH)
    assert unnamed.json()["name"] == "timer"

    listing = TimersList.model_validate(client.get("/v1/timers", headers=AUTH).json())
    assert [t.name for t in listing.timers] == ["timer", "eggs"]
    assert listing.server_time == T0

    clock.now = T0 + timedelta(seconds=420)
    app.state.services["timers"].tick()
    dismissed = client.post(f"/v1/timers/{eggs.id}/dismiss", headers=AUTH)
    assert dismissed.status_code == 200
    assert dismissed.json()["state"] == "dismissed"

    deleted = client.delete(f"/v1/timers/{unnamed.json()['id']}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "cancelled"
    assert client.get("/v1/timers", headers=AUTH).json()["timers"] == []


def test_create_rejects_bad_duration(client):
    response = client.post("/v1/timers", json={"name": "x", "duration_s": 0}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/v1/timers/t_nope/dismiss"), ("delete", "/v1/timers/t_nope")],
)
def test_unknown_timer_is_404(client, method, path):
    response = getattr(client, method)(path, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_dismiss_running_timer_is_409(client):
    timer = client.post("/v1/timers", json={"name": "x", "duration_s": 60}, headers=AUTH).json()
    response = client.post(f"/v1/timers/{timer['id']}/dismiss", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


# --- WebSocket ---------------------------------------------------------------------------


def test_events_requires_token(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/events") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_hello_then_timer_events(client, app, clock):
    existing = client.post("/v1/timers", json={"name": "rice", "duration_s": 900}, headers=AUTH)
    with ws_connect(client) as ws:
        hello = EVENT_ADAPTER.validate_python(ws.receive_json())
        assert hello.type == "hello"
        assert hello.now_playing is None
        assert hello.server_time == T0
        assert [t.id for t in hello.timers] == [existing.json()["id"]]

        assert utter(client, "set a pasta timer for 10 minutes")["say"] == (
            "Pasta timer set for 10 minutes."
        )
        created = EVENT_ADAPTER.validate_python(ws.receive_json())
        assert (created.type, created.timer.name) == ("timer.created", "pasta")

        clock.now = T0 + timedelta(minutes=10)
        client.portal.call(app.state.services["timers"].wake)
        fired = EVENT_ADAPTER.validate_python(ws.receive_json())
        assert (fired.type, fired.timer.name, fired.timer.state) == (
            "timer.fired",
            "pasta",
            "ringing",
        )

        action = utter(client, "stop", ringing=True)
        assert action["intent"] == "alarm.stop"
        assert action["commands"] == [{"type": "alarm.stop"}]
        dismissed = EVENT_ADAPTER.validate_python(ws.receive_json())
        assert (dismissed.type, dismissed.timer.name) == ("timer.dismissed", "pasta")


def test_timer_intents_end_to_end(client):
    assert utter(client, "set a timer for a quarter of an hour")["say"] == (
        "Timer set for 15 minutes."
    )
    action = utter(client, "how long left on the timer")
    assert action["intent"] == "timer.query"
    assert action["view"] == {"type": "timers"}
    assert action["say"] == "15 minutes left on the timer."


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "duration_s": 24 * 3600 + 1},
        {"name": "x", "duration_s": 10**15},
        {"name": "x" * 200, "duration_s": 60},
    ],
)
def test_create_enforces_limits(client, body):
    response = client.post("/v1/timers", json=body, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_timer"
