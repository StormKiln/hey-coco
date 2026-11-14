import httpx
import pytest

from coco_hub.qwen.client import OllamaClient, OllamaError, OllamaUnavailable, QwenHealth

from fake_ollama import MODEL, FakeOllama, down_transport

pytestmark = pytest.mark.anyio

SCHEMA = {"type": "object"}


def client(transport) -> OllamaClient:
    return OllamaClient("http://ollama.test", MODEL, transport=transport)


async def test_chat_json_returns_message_content():
    fake = FakeOllama({"intent": "none", "slots": {}, "answer": None})
    content = await client(fake.transport).chat_json("sys", "hi", SCHEMA, timeout=1)
    assert content == '{"intent": "none", "slots": {}, "answer": null}'
    assert fake.chats[0]["format"] == SCHEMA


async def test_chat_json_appends_history():
    fake = FakeOllama("ok")
    history = [{"role": "assistant", "content": "x"}, {"role": "user", "content": "again"}]
    await client(fake.transport).chat_json("sys", "hi", SCHEMA, timeout=1, history=history)
    assert [m["role"] for m in fake.chats[0]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]


async def test_chat_json_times_out():
    fake = FakeOllama(("sleep", 5))
    with pytest.raises(TimeoutError):
        await client(fake.transport).chat_json("sys", "hi", SCHEMA, timeout=0.05)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(404, json={"error": "model not found"}), OllamaUnavailable),
        (httpx.Response(500, text="boom"), OllamaError),
        (httpx.Response(200, text="not json"), OllamaError),
        (httpx.Response(200, json={"done": True}), OllamaError),
    ],
)
async def test_chat_json_errors(response, error):
    with pytest.raises(error):
        await client(FakeOllama(response).transport).chat_json("s", "u", SCHEMA, timeout=1)


async def test_chat_json_when_ollama_is_down():
    with pytest.raises(OllamaUnavailable):
        await client(down_transport()).chat_json("s", "u", SCHEMA, timeout=1)


@pytest.mark.parametrize(
    ("fake", "health"),
    [
        (FakeOllama(), QwenHealth(reachable=True, model_present=True, model_loaded=True)),
        (FakeOllama(loaded=False), QwenHealth(reachable=True, model_present=True)),
        (FakeOllama(present=False, loaded=False), QwenHealth(reachable=True)),
    ],
)
async def test_health(fake, health):
    assert await client(fake.transport).health() == health


async def test_health_ready():
    assert (await client(FakeOllama().transport).health()).ready
    assert not (await client(FakeOllama(loaded=False).transport).health()).ready


async def test_health_when_down_never_raises():
    health = await client(down_transport()).health()
    assert not health.reachable and not health.ready
    assert "unreachable" in (health.error or "")


async def test_health_matches_latest_tag():
    fake = FakeOllama()
    c = OllamaClient("http://ollama.test", "qwen3:8b", transport=fake.transport)
    assert (await c.health()).model_present
    c = OllamaClient("http://ollama.test", "qwen3", transport=fake.transport)
    assert not (await c.health()).model_present
