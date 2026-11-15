"""Spike #5: does the iPad stay in Spotify's device list while Coco holds the screen?

``make spotify-spike ARGS="--hours 10 --cold-start-every 30"`` runs this with the Spotify login
the Hub already stored (``make spotify-login``). It polls ``/me/player/devices`` every
``--interval`` seconds for ``--hours`` and records whether the configured iPad
(``spotify.device_name``) is listed and active. With ``--cold-start-every M`` it also tries to
start playback on the iPad every M minutes (with no wake help), measures how long until
Spotify reports it playing, then pauses it again.

Every record is appended (and flushed) as one JSON line to ``--out`` (default
``<data_dir>/spikes/spotify-run-<YYYYMMDD-HHMM>.jsonl``: outside git, next to the Hub's data),
so a crash or Ctrl-C keeps what was measured. At the end it prints a summary and the decision
the rule in ``docs/spikes/spotify-test-procedure.md`` gives. ``--summarize FILE`` re-prints
the summary of an earlier run.

Runs alongside the Hub: both read the tokens from the same SQLite database each time, so a
refresh by one is seen by the other.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from coco_hub.media.spotify.client import Device, SpotifyClient
from coco_hub.media.spotify.errors import SpotifyError
from coco_hub.media.spotify.service import choose_device
from coco_hub.protocol import NowPlaying
from coco_hub.settings import Settings

# The decision rule (docs/spikes/spotify-test-procedure.md).
MIN_VISIBLE_PCT = 95.0
MIN_COLD_SUCCESS_PCT = 90.0
MIN_HOURS = 6.0
MIN_COLD_ATTEMPTS = 5

CONFIRM_TIMEOUT_S = 10.0
CONFIRM_EVERY_S = 1.0


@dataclass(frozen=True)
class Poll:
    ts: datetime
    visible: bool
    """The iPad (``device_name``, else the active / only device) is in the device list."""
    active: bool
    device_id: str | None
    devices: int
    """How many devices Spotify listed."""
    error: str | None
    """The request failed: neither visible nor invisible (left out of the percentages)."""


@dataclass(frozen=True)
class ColdStart:
    ts: datetime
    ok: bool
    latency_ms: int | None
    """From sending "play" until Spotify reported it playing."""
    was_visible: bool
    error: str | None


Record = Poll | ColdStart


@dataclass(frozen=True)
class Summary:
    polls: int
    errors: int
    visible_pct: float | None
    active_pct: float | None
    longest_gap_s: float
    gaps: int
    duration_s: float
    cold_attempts: int
    cold_ok: int
    cold_success_pct: float | None
    cold_median_ms: float | None
    cold_max_ms: int | None


@dataclass(frozen=True)
class Decision:
    provider: Literal["spotify", "apple_music", "inconclusive"]
    reasons: tuple[str, ...]


class SpikeClient(Protocol):
    async def devices(self) -> list[Device]: ...

    async def play(
        self,
        device_id: str,
        *,
        context_uri: str | None = None,
        uris: Sequence[str] | None = None,
    ) -> None: ...

    async def pause(self, device_id: str | None = None) -> None: ...

    async def currently_playing(self) -> NowPlaying | None: ...


# --- JSON lines --------------------------------------------------------------------------


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dump_record(record: Record) -> str:
    if isinstance(record, Poll):
        data = {
            "type": "poll",
            "ts": _ts(record.ts),
            "visible": record.visible,
            "active": record.active,
            "device_id": record.device_id,
            "devices": record.devices,
            "error": record.error,
        }
    else:
        data = {
            "type": "cold_start",
            "ts": _ts(record.ts),
            "ok": record.ok,
            "latency_ms": record.latency_ms,
            "was_visible": record.was_visible,
            "error": record.error,
        }
    return json.dumps(data)


def write_meta(
    path: Path,
    *,
    device_name: str | None,
    interval_s: float,
    cold_every_s: float,
    started: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "device_name": device_name,
        "interval_s": interval_s,
        "cold_every_s": cold_every_s,
        "started": _ts(started),
    }
    with path.open("a") as f:
        f.write(json.dumps(meta) + "\n")


def load_records(path: Path) -> tuple[list[Record], float]:
    """The records in a run file and its poll interval (from the ``meta`` line)."""
    records: list[Record] = []
    interval_s = 60.0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        kind = data.get("type")
        if kind == "meta":
            interval_s = float(data["interval_s"])
        elif kind == "poll":
            records.append(
                Poll(
                    ts=_parse_ts(data["ts"]),
                    visible=data["visible"],
                    active=data["active"],
                    device_id=data["device_id"],
                    devices=data["devices"],
                    error=data["error"],
                )
            )
        elif kind == "cold_start":
            records.append(
                ColdStart(
                    ts=_parse_ts(data["ts"]),
                    ok=data["ok"],
                    latency_ms=data["latency_ms"],
                    was_visible=data["was_visible"],
                    error=data["error"],
                )
            )
    return records, interval_s


def default_out_path(settings: Settings, now: datetime) -> Path:
    return settings.data_dir / "spikes" / f"spotify-run-{now.strftime('%Y%m%d-%H%M')}.jsonl"


# --- summary + decision ------------------------------------------------------------------


def _pct(part: int, whole: int) -> float | None:
    return part * 100 / whole if whole else None


def summarize(records: Sequence[Record], interval_s: float) -> Summary:
    polls = sorted((r for r in records if isinstance(r, Poll)), key=lambda r: r.ts)
    colds = [r for r in records if isinstance(r, ColdStart)]
    answered = [p for p in polls if p.error is None]

    # A gap runs from the first poll that missed the iPad to the next poll that saw it; one
    # still open at the end lasts until one interval after the last poll.
    gaps: list[float] = []
    gap_start: datetime | None = None
    for p in answered:
        if not p.visible and gap_start is None:
            gap_start = p.ts
        elif p.visible and gap_start is not None:
            gaps.append((p.ts - gap_start).total_seconds())
            gap_start = None
    if gap_start is not None:
        gaps.append((answered[-1].ts - gap_start).total_seconds() + interval_s)

    latencies = [c.latency_ms for c in colds if c.ok and c.latency_ms is not None]
    stamps = [r.ts for r in records]
    return Summary(
        polls=len(polls),
        errors=len(polls) - len(answered),
        visible_pct=_pct(sum(p.visible for p in answered), len(answered)),
        active_pct=_pct(sum(p.active for p in answered), len(answered)),
        longest_gap_s=max(gaps, default=0),
        gaps=len(gaps),
        duration_s=(max(stamps) - min(stamps)).total_seconds() if stamps else 0,
        cold_attempts=len(colds),
        cold_ok=sum(c.ok for c in colds),
        cold_success_pct=_pct(sum(c.ok for c in colds), len(colds)),
        cold_median_ms=statistics.median(latencies) if latencies else None,
        cold_max_ms=max(latencies, default=None),
    )


def decide(summary: Summary) -> Decision:
    """≥ 95% visible and ≥ 90% cold starts → keep Spotify; else Apple Music (#42). Needs at
    least 6 hours of polls and 5 cold starts to say either."""
    reasons: list[str] = []
    hours = summary.duration_s / 3600
    if summary.visible_pct is None or hours < MIN_HOURS:
        reasons.append(f"run lasted {hours:.1f} h; need at least {MIN_HOURS:g} h (overnight)")
    if summary.cold_attempts < MIN_COLD_ATTEMPTS:
        reasons.append(
            f"{summary.cold_attempts} cold starts; need at least {MIN_COLD_ATTEMPTS} "
            "(--cold-start-every)"
        )
    if reasons:
        return Decision("inconclusive", tuple(reasons))
    assert summary.visible_pct is not None and summary.cold_success_pct is not None
    if summary.visible_pct < MIN_VISIBLE_PCT:
        reasons.append(f"iPad visible {summary.visible_pct:.1f}% < {MIN_VISIBLE_PCT:g}%")
    if summary.cold_success_pct < MIN_COLD_SUCCESS_PCT:
        reasons.append(
            f"cold starts worked {summary.cold_success_pct:.1f}% < {MIN_COLD_SUCCESS_PCT:g}%"
        )
    if reasons:
        return Decision("apple_music", tuple(reasons))
    return Decision(
        "spotify",
        (
            f"iPad visible {summary.visible_pct:.1f}% ≥ {MIN_VISIBLE_PCT:g}%",
            f"cold starts worked {summary.cold_success_pct:.1f}% ≥ {MIN_COLD_SUCCESS_PCT:g}%",
        ),
    )


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _fmt_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def format_summary(summary: Summary, decision: Decision) -> str:
    verdict = {
        "spotify": "keep Spotify",
        "apple_music": 'switch to Apple Music (music.provider = "apple_music", #42)',
        "inconclusive": "inconclusive: run longer / with cold starts",
    }[decision.provider]
    median = "n/a" if summary.cold_median_ms is None else f"{summary.cold_median_ms:.0f} ms"
    worst = "n/a" if summary.cold_max_ms is None else f"{summary.cold_max_ms} ms"
    lines = [
        "Spotify spike summary (#5)",
        f"  duration:            {_fmt_duration(summary.duration_s)}",
        f"  polls:               {summary.polls} ({summary.errors} failed)",
        f"  iPad visible:        {_fmt_pct(summary.visible_pct)}",
        f"  iPad active:         {_fmt_pct(summary.active_pct)}",
        f"  longest gap:         {_fmt_duration(summary.longest_gap_s)} ({summary.gaps} gaps)",
        f"  cold starts:         {summary.cold_ok}/{summary.cold_attempts} "
        f"({_fmt_pct(summary.cold_success_pct)})",
        f"  cold-start latency:  median {median}, worst {worst}",
        f"Decision: {verdict}",
        *(f"  - {reason}" for reason in decision.reasons),
    ]
    return "\n".join(lines)


# --- the run -----------------------------------------------------------------------------


async def _poll(client: SpikeClient, device_name: str | None, now: datetime) -> Poll:
    try:
        devices = await client.devices()
    except SpotifyError as exc:
        return Poll(now, False, False, None, 0, f"{type(exc).__name__}: {exc}")
    device = choose_device(devices, device_name)
    return Poll(
        ts=now,
        visible=device is not None,
        active=bool(device and device.is_active),
        device_id=device.id if device else None,
        devices=len(devices),
        error=None,
    )


async def _cold_start(
    client: SpikeClient,
    device_name: str | None,
    uri: str | None,
    now: datetime,
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> ColdStart:
    try:
        device = choose_device(await client.devices(), device_name)
    except SpotifyError as exc:
        return ColdStart(now, False, None, False, f"{type(exc).__name__}: {exc}")
    if device is None:
        return ColdStart(now, False, None, False, "device not visible")
    started = monotonic()
    try:
        if uri is None:
            await client.play(device.id)
        elif uri.startswith("spotify:track:"):
            await client.play(device.id, uris=[uri])
        else:
            await client.play(device.id, context_uri=uri)
        while True:
            current = await client.currently_playing()
            elapsed = monotonic() - started
            if current is not None and current.is_playing:
                return ColdStart(now, True, round(elapsed * 1000), True, None)
            if elapsed >= CONFIRM_TIMEOUT_S:
                return ColdStart(
                    now, False, None, True, f"not playing after {CONFIRM_TIMEOUT_S:g}s"
                )
            await sleep(CONFIRM_EVERY_S)
    except SpotifyError as exc:
        return ColdStart(now, False, None, True, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await client.pause(device.id)
        except SpotifyError:
            pass  # already paused, or the device went away: nothing to undo


async def run_spike(
    client: SpikeClient,
    device_name: str | None,
    *,
    duration_s: float,
    interval_s: float,
    cold_every_s: float,
    out: Path,
    uri: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    echo: Callable[[str], None] = lambda line: None,
) -> Summary:
    """Poll (and cold-start) until ``duration_s`` has passed; returns the summary."""
    write_meta(
        out,
        device_name=device_name,
        interval_s=interval_s,
        cold_every_s=cold_every_s,
        started=clock(),
    )
    records: list[Record] = []
    start = monotonic()
    next_cold = 0.0
    tick = 0
    with out.open("a") as f:

        def keep(record: Record) -> None:
            records.append(record)
            line = dump_record(record)
            f.write(line + "\n")
            f.flush()
            echo(line)

        while tick * interval_s < duration_s:
            keep(await _poll(client, device_name, clock()))
            if cold_every_s > 0 and monotonic() - start >= next_cold:
                keep(await _cold_start(client, device_name, uri, clock(), monotonic, sleep))
                next_cold += cold_every_s
            tick += 1
            wait = start + tick * interval_s - monotonic()
            if tick * interval_s < duration_s and wait > 0:
                await sleep(wait)
    return summarize(records, interval_s)


# --- CLI ---------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make spotify-spike ARGS=...",
        description="Spike #5: is the iPad listed by Spotify while Coco holds the screen?",
    )
    parser.add_argument("--hours", type=float, default=10.0, help="how long to run (10)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between polls")
    parser.add_argument(
        "--cold-start-every",
        type=float,
        default=0.0,
        metavar="MINUTES",
        help="also start playback on the iPad every MINUTES, then pause it (0 = off)",
    )
    parser.add_argument(
        "--uri",
        help="what cold starts play (spotify:track:/album:/playlist:); default: resume",
    )
    parser.add_argument("--out", type=Path, help="JSON-lines log (default: data_dir/spikes/)")
    parser.add_argument("--summarize", type=Path, metavar="FILE", help="summarize a past run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.summarize is not None:
        records, interval_s = load_records(args.summarize)
        summary = summarize(records, interval_s)
        print(format_summary(summary, decide(summary)))
        return 0
    return asyncio.run(_run_cli(args))


async def _run_cli(args: argparse.Namespace) -> int:
    from coco_hub import db
    from coco_hub.media.spotify.service import SpotifyService
    from coco_hub.settings import load_settings

    settings = load_settings()
    conn = db.open_db(settings.data_dir)
    service = SpotifyService.from_settings(conn, settings)
    try:
        service.ensure_ready()
    except SpotifyError as exc:
        print(f"{exc.say} (see docs/spotify.md)", file=sys.stderr)
        return 1
    # Raw Web API calls: the spike measures Spotify without the Hub's wake help.
    client = SpotifyClient(service.auth)
    out = args.out or default_out_path(settings, datetime.now())
    print(f"Logging to {out}; Ctrl-C stops early and still prints the summary.")
    try:
        await run_spike(
            client,
            settings.spotify.device_name,
            duration_s=args.hours * 3600,
            interval_s=args.interval,
            cold_every_s=args.cold_start_every * 60,
            uri=args.uri,
            out=out,
            echo=print,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped early.")
    finally:
        await client.aclose()
        await service.aclose()
        conn.close()
    records, interval_s = load_records(out)
    summary = summarize(records, interval_s)
    print(format_summary(summary, decide(summary)))
    print(f"Log: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
