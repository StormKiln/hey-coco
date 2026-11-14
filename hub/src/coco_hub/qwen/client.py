"""Async Ollama client for the Qwen fallback.

Request settings come from the Qwen spike (docs/spikes/qwen.md): ``/api/chat`` with a JSON
schema in ``format`` (grammar-constrained decoding), ``think: false``, ``temperature: 0``,
``num_ctx: 4096`` and ``keep_alive: -1`` so the model stays resident.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_OPTIONS: Mapping[str, Any] = {"temperature": 0, "num_ctx": 4096, "num_predict": 200}


class OllamaError(Exception):
    """Ollama answered, but not with something usable."""


class OllamaUnavailable(OllamaError):
    """Ollama isn't reachable, or doesn't have the model."""


@dataclass(frozen=True)
class QwenHealth:
    reachable: bool
    model_present: bool = False
    model_loaded: bool = False
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_present and self.model_loaded


def _same_model(name: str, model: str) -> bool:
    """``qwen3:8b`` == ``qwen3:8b``; a bare ``qwen3`` means ``qwen3:latest``."""

    def tag(n: str) -> str:
        return n if ":" in n else f"{n}:latest"

    return tag(name) == tag(model)


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        options: Mapping[str, Any] = DEFAULT_OPTIONS,
    ) -> None:
        self.model = model
        self.options = dict(options)
        # Deadlines are enforced per call with asyncio.timeout; no httpx timeout on top.
        self._http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=None)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise OllamaUnavailable(f"Ollama unreachable: {exc!r}") from exc
        if response.status_code == 404:
            raise OllamaUnavailable(f"Ollama {path} 404: {response.text[:200]}")
        if response.status_code >= 400:
            raise OllamaError(f"Ollama {path} {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama {path} returned non-JSON") from exc

    async def chat_json(
        self,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        timeout: float,
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str:
        """The model's reply text (meant to be JSON matching ``schema``; not validated here).

        ``history`` is appended after the user turn (e.g. a bad reply and a repair request).
        Raises ``TimeoutError`` after ``timeout`` seconds, ``OllamaUnavailable`` when Ollama
        is down or lacks the model, ``OllamaError`` otherwise.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
                *history,
            ],
            "format": schema,
            "think": False,
            "stream": False,
            "keep_alive": -1,
            "options": self.options,
        }
        async with asyncio.timeout(timeout):
            data = await self._request("POST", "/api/chat", json=payload)
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError("Ollama /api/chat reply has no message content") from exc
        if not isinstance(content, str):
            raise OllamaError("Ollama /api/chat message content is not text")
        return content

    async def health(self, timeout: float = 2.0) -> QwenHealth:
        """Whether Ollama is up, has the model, and has it loaded in memory. Never raises."""
        try:
            async with asyncio.timeout(timeout):
                tags = await self._request("GET", "/api/tags")
                ps = await self._request("GET", "/api/ps")
        except (OllamaError, TimeoutError) as exc:
            return QwenHealth(reachable=False, error=str(exc) or type(exc).__name__)

        def names(data: Any) -> list[str]:
            models = data.get("models") if isinstance(data, dict) else None
            return [m.get("name", "") for m in models or [] if isinstance(m, dict)]

        return QwenHealth(
            reachable=True,
            model_present=any(_same_model(n, self.model) for n in names(tags)),
            model_loaded=any(_same_model(n, self.model) for n in names(ps)),
        )
