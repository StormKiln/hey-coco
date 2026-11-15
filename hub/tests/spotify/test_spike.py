"""Spike #5 tooling: poll the iPad's visibility, cold-start playback, summarize, decide."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from coco_hub import db
from coco_hub.media.spotify import spike
from coco_hub.media.spotify.auth import SpotifyAuth
from coco_hub.media.spotify.client import SpotifyClient
from coco_hub.media.spotify.spike import (
    ColdStart,
    Decision,
    Poll,
    decide,
    format_summary,
    load_records,
    run_spike,
    summarize,
)

from fake_spotify import IPAD_ID, FakeSpotify, json_response, log_in

T0 = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def poll(minutes: float, visible: bool, *, active: bool = False, error: str | None = None) -> Poll:
    return Poll(
        ts=at(minutes),
        visible=visible,
        active=active,
        device_id=IPAD_ID if visible else None,
        devices=2 if visible else 1,
        error=error,
    )


def cold(minutes: float, ok: bool, latency_ms: int | None = None) -> ColdStart:
    return ColdStart(
        ts=at(minutes),
        ok=ok,
        latency_ms=latency_ms,
        was_visible=ok,
        error=None if ok else "device not visible",
    )


# --- summary math ------------------------------------------------------------------------


def test_visibility_and_longest_gap():
    records = [
        poll(0, True),
        poll(1, False),
        poll(2, False),
        poll(3, True),
        poll(4, False),
        poll(5, True),
        poll(6, True),
        poll(7, True),
    ]
    s = summarize(records, interval_s=60)
    assert s.polls == 8
    assert s.errors == 0
    assert s.visible_pct == pytest.approx(62.5)
    assert s.longest_gap_s == 120  # invisible at 1 min, back at 3 min
    assert s.gaps == 2
    assert s.duration_s == 7 * 60
    assert s.cold_attempts == 0
    assert s.cold_success_pct is None


def test_gap_open_at_the_end_runs_one_interval_past_the_last_poll():
    s = summarize([poll(0, True), poll(1, False), poll(2, False)], interval_s=60)
    assert s.longest_gap_s == 120
    assert s.gaps == 1


def test_failed_polls_are_not_counted_as_invisible():
    records = [poll(0, True), poll(1, False, error="SpotifyError: HTTP 502"), poll(2, True)]
    s = summarize(records, interval_s=60)
    assert s.errors == 1
    assert s.visible_pct == 100
    assert s.longest_gap_s == 0
    assert s.gaps == 0


def test_cold_start_rate_and_latency():
    records = [
        poll(0, True),
        cold(0, True, 900),
        cold(30, True, 1500),
        cold(60, False),
        cold(90, True, 1200),
    ]
    s = summarize(records, interval_s=60)
    assert s.cold_attempts == 4
    assert s.cold_ok == 3
    assert s.cold_success_pct == 75
    assert s.cold_median_ms == 1200
    assert s.cold_max_ms == 1500


def test_empty_log():
    s = summarize([], interval_s=60)
    assert s.polls == 0
    assert s.visible_pct is None
    assert decide(s).provider == "inconclusive"


# --- decision rule -----------------------------------------------------------------------


def _summary(visible: int, cold_ok: int, cold_attempts: int, hours: float = 8):
    polls = int(hours * 60)
    visible_polls = round(polls * visible / 100)
    records = [poll(i, i < visible_polls) for i in range(polls)]
    records += [cold(i * 30, i < cold_ok, 1000) for i in range(cold_attempts)]
    return summarize(records, interval_s=60)


def test_keep_spotify_when_visible_and_cold_starts_work():
    d = decide(_summary(visible=96, cold_ok=19, cold_attempts=20))
    assert d == Decision(provider="spotify", reasons=d.reasons)
    assert d.provider == "spotify"


def test_apple_music_when_the_ipad_drops_off():
    d = decide(_summary(visible=80, cold_ok=20, cold_attempts=20))
    assert d.provider == "apple_music"
    assert any("visible" in r for r in d.reasons)


def test_apple_music_when_cold_starts_fail():
    d = decide(_summary(visible=99, cold_ok=10, cold_attempts=20))
    assert d.provider == "apple_music"
    assert any("cold" in r for r in d.reasons)


def test_inconclusive_without_cold_starts_or_a_long_enough_run():
    assert decide(_summary(visible=100, cold_ok=0, cold_attempts=0)).provider == "inconclusive"
    assert (
        decide(_summary(visible=100, cold_ok=5, cold_attempts=5, hours=1)).provider
        == "inconclusive"
    )


def test_format_summary_mentions_the_numbers():
    s = _summary(visible=96, cold_ok=19, cold_attempts=20)
    text = format_summary(s, decide(s))
    assert "96.0%" in text
    assert "95.0%" in text  # cold-start success
    assert "keep Spotify" in text


# --- the runner, against the fake Spotify ------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = T0

    def clock(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return (self.now - T0).total_seconds()

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path)
    yield c
    c.close()


@pytest.fixture
def fake() -> FakeSpotify:
    return FakeSpotify()


@pytest.fixture
def client(conn, fake):
    log_in(conn, T0)
    time = FakeTime()
    auth = SpotifyAuth(
        conn,
        client_id="client-123",
        redirect_uri="https://127.0.0.1:8765/spotify/callback",
        clock=time.clock,
        transport=fake.transport,
    )
    return SpotifyClient(auth, transport=fake.transport, sleep=time.sleep), time


@pytest.mark.anyio
async def test_run_polls_for_the_duration_and_writes_jsonl(client, fake, tmp_path):
    spotify, time = client
    fake.queue("GET", "/v1/me/player/devices", json_response(200, "devices"))
    fake.queue("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    out = tmp_path / "run.jsonl"
    summary = await run_spike(
        spotify,
        "Kitchen iPad",
        duration_s=300,
        interval_s=60,
        cold_every_s=0,
        out=out,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[0]["type"] == "meta"
    assert lines[0]["device_name"] == "Kitchen iPad"
    polls = [line for line in lines if line["type"] == "poll"]
    assert len(polls) == 5
    assert [p["visible"] for p in polls] == [True, False, True, True, True]
    assert polls[0]["ts"] == "2026-11-14T22:00:00Z"
    assert fake.calls("PUT", "/v1/me/player/play") == []
    assert summary.polls == 5
    assert summary.visible_pct == 80
    # The file alone gives the same summary.
    assert summarize(*load_records(out)) == summary


@pytest.mark.anyio
async def test_cold_start_plays_measures_and_pauses(client, fake, tmp_path):
    spotify, time = client
    fake.queue(
        "GET",
        "/v1/me/player/currently-playing",
        json_response(204),
        json_response(200, "currently_playing"),
    )
    out = tmp_path / "run.jsonl"
    summary = await run_spike(
        spotify,
        "Kitchen iPad",
        duration_s=60,
        interval_s=60,
        cold_every_s=60,
        uri="spotify:track:3BananaPhoneTrackId000a",
        out=out,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    (play, *_) = fake.calls("PUT", "/v1/me/player/play")
    assert play.url.params["device_id"] == IPAD_ID
    assert fake.body(play) == {"uris": ["spotify:track:3BananaPhoneTrackId000a"]}
    assert fake.calls("PUT", "/v1/me/player/pause")
    colds = [json.loads(x) for x in out.read_text().splitlines() if '"cold_start"' in x]
    assert colds[0]["ok"] is True
    assert colds[0]["latency_ms"] == 1000  # confirmed on the second check, 1 s later
    assert summary.cold_attempts == 1
    assert summary.cold_ok == 1


@pytest.mark.anyio
async def test_cold_start_with_the_ipad_missing_fails_without_playing(client, fake, tmp_path):
    spotify, time = client
    fake.set("GET", "/v1/me/player/devices", json_response(200, "devices_no_ipad"))
    out = tmp_path / "run.jsonl"
    summary = await run_spike(
        spotify,
        "Kitchen iPad",
        duration_s=60,
        interval_s=60,
        cold_every_s=60,
        out=out,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    assert fake.calls("PUT", "/v1/me/player/play") == []
    assert summary.cold_attempts == 1
    assert summary.cold_ok == 0
    cold_line = next(json.loads(x) for x in out.read_text().splitlines() if "cold_start" in x)
    assert cold_line["error"] == "device not visible"


@pytest.mark.anyio
async def test_cold_start_that_never_plays_times_out(client, fake, tmp_path):
    spotify, time = client
    out = tmp_path / "run.jsonl"
    summary = await run_spike(
        spotify,
        "Kitchen iPad",
        duration_s=60,
        interval_s=60,
        cold_every_s=60,
        out=out,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    assert summary.cold_ok == 0
    cold_line = next(json.loads(x) for x in out.read_text().splitlines() if "cold_start" in x)
    assert "not playing" in cold_line["error"]


@pytest.mark.anyio
async def test_poll_errors_are_recorded_not_fatal(client, fake, tmp_path):
    spotify, time = client
    fake.queue("GET", "/v1/me/player/devices", httpx_502())
    out = tmp_path / "run.jsonl"
    summary = await run_spike(
        spotify,
        "Kitchen iPad",
        duration_s=120,
        interval_s=60,
        cold_every_s=0,
        out=out,
        clock=time.clock,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    assert summary.polls == 2
    assert summary.errors == 1


def httpx_502():
    return json_response(502, json={"error": {"status": 502, "message": "Bad gateway"}})


def test_default_out_path_is_under_data_dir(settings):
    path = spike.default_out_path(settings, T0)
    assert path == settings.data_dir / "spikes" / "spotify-run-20261114-2200.jsonl"


def test_summarize_cli(tmp_path, capsys):
    out = tmp_path / "run.jsonl"
    spike.write_meta(out, device_name="Kitchen iPad", interval_s=60, cold_every_s=0, started=T0)
    with out.open("a") as f:
        for record in [poll(0, True), poll(1, False), poll(2, True)]:
            f.write(spike.dump_record(record) + "\n")
    assert spike.main(["--summarize", str(out)]) == 0
    text = capsys.readouterr().out
    assert "66.7%" in text
