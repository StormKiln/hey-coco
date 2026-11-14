"""A canned Ollama for tests, served through ``httpx.MockTransport`` (no live Ollama)."""

import asyncio
import json
from typing import Any

import httpx

MODEL = "qwen3:8b"


def chat_reply(content: str | dict[str, Any]) -> dict[str, Any]:
    """An ``/api/chat`` response body as Ollama 0.40 sends it (stream: false)."""
    if not isinstance(content, str):
        content = json.dumps(content)
    return {
        "model": MODEL,
        "created_at": "2026-11-14T15:00:00.000000Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "total_duration": 470_000_000,
        "eval_count": 18,
    }


TAGS = {"models": [{"name": MODEL, "model": MODEL, "size": 5_225_388_164}]}
PS_LOADED = {"models": [{"name": MODEL, "model": MODEL, "expires_at": "2318-01-01T00:00:00Z"}]}
PS_EMPTY: dict[str, Any] = {"models": []}


class FakeOllama:
    """Replies to ``/api/chat`` from a queue (``replies``), records every request.

    A reply is the model's message content (str or dict), an ``httpx.Response``, an
    exception to raise, or ``("sleep", seconds)`` to hang.
    """

    def __init__(self, *replies: Any, loaded: bool = True, present: bool = True) -> None:
        self.replies = list(replies)
        self.loaded = loaded
        self.present = present
        self.chats: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS if self.present else {"models": []})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json=PS_LOADED if self.loaded else PS_EMPTY)
        assert request.url.path == "/api/chat", request.url.path
        body = json.loads(request.content)
        self.chats.append(body)
        if not self.replies:
            raise AssertionError(f"unexpected /api/chat call: {body['messages'][-1]}")
        reply = self.replies.pop(0)
        if isinstance(reply, tuple) and reply[0] == "sleep":
            await asyncio.sleep(reply[1])
            return httpx.Response(200, json=chat_reply({"intent": "none", "slots": {}}))
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=chat_reply(reply))


def down_transport() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 61] Connection refused", request=request)

    return httpx.MockTransport(refuse)
