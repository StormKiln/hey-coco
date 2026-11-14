"""Run the real Hub (``python -m coco_hub``) as a subprocess for resilience checks.

Each Hub gets a temp config: a random free port on loopback, temp data/log dirs, discovery
off, a generated token, Qwen pointed at a dead localhost port (Ollama "down") and Open-Meteo
pointed at a dead localhost port (internet "down"). Nothing here touches the network beyond
127.0.0.1, the host's firewall, launchd or a real Ollama.
"""

import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.sync.client import ClientConnection, connect

from coco_hub.db import DB_FILENAME

STARTUP_TIMEOUT_S = 20.0


def free_port() -> int:
    """A port nothing listens on (the socket is closed again before returning)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BlackHole:
    """A TCP listener that accepts connections (in the kernel backlog) but never answers:
    what a dead upstream behind a working LAN looks like (requests hang until timeout)."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port: int = self._sock.getsockname()[1]

    def close(self) -> None:
        self._sock.close()


class Hub:
    """One Hub instance: its config, data dir and (when running) its process."""

    def __init__(self, root: Path, *, weather_url: str, ollama_url: str) -> None:
        self.root = root
        self.port = free_port()
        self.token = secrets.token_urlsafe(32)
        self.data_dir = root / "data"
        self.log_dir = root / "logs"
        self.config = root / "config.toml"
        self.config.write_text(
            f"""
token = "{self.token}"
host = "127.0.0.1"
port = {self.port}
data_dir = "{self.data_dir}"
log_dir = "{self.log_dir}"
timezone = "UTC"

[location]
lat = 45.42
lon = -75.69
name = "Test"

[weather]
forecast_url = "{weather_url}"

[qwen]
enabled = true
base_url = "{ollama_url}"
timeout_s = 6.0

[discovery]
enabled = false
"""
        )
        self.proc: subprocess.Popen[bytes] | None = None
        self._out = None
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.ws_url = f"ws://127.0.0.1:{self.port}/v1/events"

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILENAME

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        assert self.proc is None or self.proc.poll() is not None, "already running"
        env = {k: v for k, v in os.environ.items() if not k.startswith("COCO_")}
        env["COCO_CONFIG"] = str(self.config)
        env["PYTHONUNBUFFERED"] = "1"
        self._out = (self.root / "hub.out").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "coco_hub"],
            env=env,
            stdout=self._out,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"Hub exited at startup ({self.proc.returncode}):\n{self.output()}"
                )
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.kill()
        raise RuntimeError(f"Hub did not come up in {STARTUP_TIMEOUT_S}s:\n{self.output()}")

    def kill(self) -> None:
        """SIGKILL: no shutdown handlers run (a crash, or the power going)."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)
        self._close_out()

    def terminate(self, timeout: float = 5.0) -> int:
        """SIGTERM (what launchd sends) and wait; returns the exit code."""
        assert self.proc is not None
        self.proc.send_signal(signal.SIGTERM)
        try:
            return self.proc.wait(timeout=timeout)
        finally:
            self._close_out()

    def _close_out(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    def output(self) -> str:
        path = self.root / "hub.out"
        return path.read_text(errors="replace")[-4000:] if path.exists() else ""

    # --- HTTP ----------------------------------------------------------------------------

    def get(self, path: str, timeout: float = 10.0) -> httpx.Response:
        return httpx.get(f"{self.base_url}{path}", headers=self.headers, timeout=timeout)

    def post(self, path: str, json: Any, timeout: float = 10.0) -> httpx.Response:
        return httpx.post(
            f"{self.base_url}{path}", headers=self.headers, json=json, timeout=timeout
        )

    def say(self, text: str, timeout: float = 10.0) -> dict[str, Any]:
        body = {
            "text": text,
            "source": "voice",
            "context": {"view": "idle", "alarm_ringing": False, "media_playing": None},
        }
        response = self.post("/v1/utterance", body, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def timers(self) -> list[dict[str, Any]]:
        response = self.get("/v1/timers")
        response.raise_for_status()
        return response.json()["timers"]

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection to the Hub's database: committed, then closed."""
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def shutdown_logged(self) -> bool:
        log = self.log_dir / "hub.jsonl"
        return log.exists() and "Application shutdown complete." in log.read_text()

    @contextmanager
    def events(self, timeout: float = 5.0) -> Iterator[tuple[ClientConnection, dict[str, Any]]]:
        """Connect to ``WS /v1/events``; yields the socket and its ``hello`` event."""
        with connect(self.ws_url, additional_headers=self.headers, open_timeout=timeout) as ws:
            hello = json.loads(ws.recv(timeout=timeout))
            assert hello["type"] == "hello"
            yield ws, hello


@pytest.fixture
def dead_url() -> str:
    """An http URL on a localhost port nothing listens on (connection refused)."""
    return f"http://127.0.0.1:{free_port()}"


@pytest.fixture
def black_hole() -> Iterator[BlackHole]:
    hole = BlackHole()
    yield hole
    hole.close()


@pytest.fixture
def make_hub(tmp_path: Path, dead_url: str) -> Iterator[Any]:
    """``make_hub(weather_url=..., ollama_url=...)``: an unstarted Hub; killed at teardown."""
    hubs: list[Hub] = []

    def make(*, weather_url: str | None = None, ollama_url: str | None = None) -> Hub:
        root = tmp_path / f"hub{len(hubs)}"
        root.mkdir()
        hub = Hub(
            root,
            weather_url=weather_url or f"{dead_url}/v1/forecast",
            ollama_url=ollama_url or dead_url,
        )
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        hub.kill()


@pytest.fixture
def hub(make_hub: Any) -> Hub:
    """A running Hub with the internet and Ollama both down (connection refused)."""
    h = make_hub()
    h.start()
    return h
