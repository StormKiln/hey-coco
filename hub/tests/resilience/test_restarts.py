"""The Hub process dies or restarts: timers survive, the DB stays sound, clients resync."""

import json
import signal
import time
from datetime import UTC, datetime, timedelta

import pytest
from websockets.exceptions import ConnectionClosed

pytestmark = pytest.mark.resilience

# Uvicorn re-raises SIGTERM after a graceful shutdown, so a clean exit reads as -SIGTERM.
CLEAN_EXIT = {0, -signal.SIGTERM}


def _ts(dt: datetime) -> str:
    """The timers table's timestamp format (``coco_hub.timers.service._ts``)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(interval)


def integrity_ok(hub) -> bool:
    with hub.db() as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def assert_clean_stop(hub) -> None:
    started = time.monotonic()
    assert hub.terminate(timeout=5.0) in CLEAN_EXIT
    assert time.monotonic() - started < 5.0
    assert hub.shutdown_logged()  # the lifespan's shutdown ran
    # Closing the last connection checkpoints the WAL into the DB and removes it (or, with
    # macOS's persistent-WAL SQLite, truncates it to 0 bytes): the Hub closed its DB.
    wal = hub.db_path.with_name(hub.db_path.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    assert integrity_ok(hub)


# --- 1. Hub killed mid-timer ------------------------------------------------------------


def test_timer_that_ends_while_hub_is_killed_rings_after_restart(hub):
    created = hub.post("/v1/timers", {"name": "pasta", "duration_s": 3}).json()
    hub.kill()  # SIGKILL: no shutdown handlers
    time.sleep(5)
    assert integrity_ok(hub)

    hub.start()
    ringing = wait_for(lambda: [t for t in hub.timers() if t["state"] == "ringing"])
    assert [t["id"] for t in ringing] == [created["id"]]
    with hub.events() as (_, hello):
        assert [(t["id"], t["state"]) for t in hello["timers"]] == [(created["id"], "ringing")]


def test_timer_missed_by_more_than_grace_is_dismissed_silently(hub):
    hub.terminate()
    ended = datetime.now(UTC) - timedelta(hours=2)  # default missed_grace_s is 1 h
    created = ended - timedelta(minutes=10)
    with hub.db() as conn:
        conn.execute(
            "INSERT INTO timers (id, name, duration_s, created_at, ends_at, state, updated_at) "
            "VALUES ('t_old', 'roast', 600, ?, ?, 'running', ?)",
            (_ts(created), _ts(ended), _ts(created)),
        )

    hub.start()

    def state() -> str:
        with hub.db() as conn:
            return conn.execute("SELECT state FROM timers WHERE id = 't_old'").fetchone()[0]

    assert wait_for(lambda: state() == "dismissed")
    assert hub.timers() == []
    with hub.events() as (_, hello):
        assert hello["timers"] == []


# --- 4. Graceful restart ----------------------------------------------------------------


def test_sigterm_exits_cleanly_and_restart_serves_the_same_timers(hub):
    pasta = hub.post("/v1/timers", {"name": "pasta", "duration_s": 600}).json()
    eggs = hub.post("/v1/timers", {"name": "eggs", "duration_s": 300}).json()
    before = hub.timers()
    assert {t["id"] for t in before} == {pasta["id"], eggs["id"]}

    assert_clean_stop(hub)

    hub.start()
    assert hub.timers() == before


def test_sigterm_with_a_connected_event_client_still_exits_cleanly(hub):
    with hub.events():
        assert_clean_stop(hub)


# --- 5. Event stream reconnect ----------------------------------------------------------


def test_event_client_reconnects_after_restart_and_gets_fresh_hello(hub):
    with hub.events() as (ws, hello):
        assert hello["timers"] == []
        timer = hub.post("/v1/timers", {"name": "rice", "duration_s": 900}).json()
        created = json.loads(ws.recv(timeout=5))
        assert (created["type"], created["timer"]["id"]) == ("timer.created", timer["id"])

        hub.terminate()
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=5)

    hub.start()
    with hub.events() as (ws, hello):
        assert [(t["id"], t["state"]) for t in hello["timers"]] == [(timer["id"], "running")]
        # Live again: a new timer arrives as an event on the new connection.
        hub.post("/v1/timers", {"name": "tea", "duration_s": 120})
        event = json.loads(ws.recv(timeout=5))
        assert (event["type"], event["timer"]["name"]) == ("timer.created", "tea")


def test_event_client_reconnects_after_a_hub_kill(hub):
    with hub.events() as (ws, _):
        timer = hub.post("/v1/timers", {"name": "rice", "duration_s": 900}).json()
        assert json.loads(ws.recv(timeout=5))["type"] == "timer.created"
        hub.kill()
        with pytest.raises((ConnectionClosed, OSError)):
            ws.recv(timeout=5)

    hub.start()
    with hub.events() as (_, hello):
        assert [t["id"] for t in hello["timers"]] == [timer["id"]]


# --- TLS identity (#54) -----------------------------------------------------------------


def test_tls_identity_survives_kill_and_restart(hub):
    """A pinned iPad keeps working after a crash: same certificate, same Hub ID."""
    before = hub.get("/healthz").json()
    cert = hub.cert_path.read_bytes()
    hub.kill()
    hub.start()  # verifies against the same certificate file
    assert hub.get("/healthz").json() == before
    assert hub.cert_path.read_bytes() == cert
    assert before["fingerprint"] is not None


def test_plain_http_hub_when_tls_is_disabled(make_hub):
    plain = make_hub(tls=False)
    plain.start()
    assert plain.base_url.startswith("http://")
    assert plain.get("/healthz").json()["fingerprint"] is None
    with plain.events() as (_, hello):
        assert hello["timers"] == []
