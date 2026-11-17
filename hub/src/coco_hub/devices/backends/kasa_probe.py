"""Spike #132: probe one Kasa plug or power strip, read-only unless asked.

``make kasa-probe HOST=<ip>`` asks the device at that address (one unicast query: no network
scan) for its model, hardware/firmware version, alias, outlets and on/off state, says which
local protocol it uses and whether that needs the TP-Link login, and times ``--reads`` state
reads (p50/p95). Nothing is switched unless you pass ``--toggle`` (``ARGS="--toggle"``), which
flips the device, or one outlet (``--child 0`` or ``--child "<outlet alias>"``), and back.

Credentials, only for KLAP/AES devices, come from ``[backends.kasa]`` ``username`` /
``password`` in ``hub/config.local.toml`` (or ``COCO_BACKENDS__KASA__USERNAME`` / ``...PASSWORD``)
and are never printed. Record findings in docs/spikes/kasa.md without household details.
"""

import argparse
import asyncio
import math
import os
import statistics
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from coco_hub.devices.backends import kasa as kasa_backend
from coco_hub.devices.errors import AuthRequired, DeviceError, Unreachable, Unsupported
from coco_hub.settings import config_path


@dataclass(frozen=True)
class ToggleResult:
    target: str
    before: bool
    after_flip: bool
    after_restore: bool
    flip_ms: float
    restore_ms: float

    @property
    def ok(self) -> bool:
        return self.after_flip == (not self.before) and self.after_restore == self.before


@dataclass(frozen=True)
class ProbeResult:
    info: kasa_backend.KasaInfo
    used_credentials: bool
    connect_ms: float
    reads_ms: tuple[float, ...]
    toggle: ToggleResult | None


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (p50 of 4 values is the 2nd smallest)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


async def run_probe(
    host: str,
    *,
    credentials: tuple[str, str] | None = None,
    reads: int = 10,
    toggle: bool = False,
    child: str | None = None,
    timeout_s: float = 2.0,
    discover: kasa_backend.DiscoverSingle | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> ProbeResult:
    start = clock()
    device = await kasa_backend.connect(
        host, credentials=credentials, timeout_s=timeout_s, discover=discover
    )
    connect_ms = (clock() - start) * 1000
    try:
        info = kasa_backend.describe(device)
        reads_ms = []
        for _ in range(reads):
            t0 = clock()
            await _call(device.update())
            reads_ms.append((clock() - t0) * 1000)
        result = None
        if toggle:
            target = kasa_backend.find_child(device, child) if child is not None else device
            result = await _toggle(device, target, clock)
        return ProbeResult(info, credentials is not None, connect_ms, tuple(reads_ms), result)
    finally:
        await kasa_backend.disconnect(device)


async def _call(awaitable) -> None:
    try:
        await awaitable
    except Exception as exc:
        raise kasa_backend.translate(exc) from exc


async def _toggle(device, target, clock: Callable[[], float]) -> ToggleResult:
    before = bool(target.is_on)

    async def switch(on: bool) -> tuple[bool, float]:
        t0 = clock()
        await _call(target.turn_on() if on else target.turn_off())
        await _call(device.update())
        return bool(target.is_on), (clock() - t0) * 1000

    after_flip, flip_ms = await switch(not before)
    after_restore, restore_ms = await switch(before)
    return ToggleResult(target.alias or "", before, after_flip, after_restore, flip_ms, restore_ms)


def _onoff(value: bool) -> str:
    return "on" if value else "off"


def format_report(host: str, result: ProbeResult) -> str:
    info = result.info
    login = "needs the TP-Link login" if info.needs_credentials else "no TP-Link login needed"
    lines = [
        f"Host:         {host}",
        f"Model:        {info.model}  (hw {info.hw_ver}, fw {info.fw_ver})",
        f"MAC:          {info.mac}",
        f"Alias:        {info.alias!r}",
        f"Protocol:     {info.protocol}: {login}",
        f"Credentials:  {'used (from config, not shown)' if result.used_credentials else 'none'}",
        f"State:        {_onoff(info.is_on)}",
    ]
    if info.outlets:
        lines.append(f"Outlets ({len(info.outlets)}):")
        lines += [f"  {o.index}: {o.alias!r} {_onoff(o.is_on)}" for o in info.outlets]
    lines.append(f"Connect:      {result.connect_ms:.0f} ms (discovery + first read)")
    if result.reads_ms:
        r = result.reads_ms
        lines.append(
            f"State reads:  {len(r)}, p50 {percentile(r, 50):.0f} ms, "
            f"p95 {percentile(r, 95):.0f} ms, mean {statistics.fmean(r):.0f} ms"
        )
    if (t := result.toggle) is not None:
        verdict = "OK" if t.ok else "FAILED: the device did not report the expected state"
        lines.append(
            f"Toggle {t.target!r}: {_onoff(t.before)} -> {_onoff(t.after_flip)} "
            f"({t.flip_ms:.0f} ms) -> {_onoff(t.after_restore)} ({t.restore_ms:.0f} ms): {verdict}"
        )
    return "\n".join(lines)


def configured_credentials() -> tuple[str, str] | None:
    """``[backends.kasa]`` username/password from the config file, overridden by env."""
    section: dict = {}
    path = config_path()
    if path.is_file():
        with path.open("rb") as f:
            section = tomllib.load(f).get("backends", {}).get("kasa", {}) or {}
    username = os.environ.get("COCO_BACKENDS__KASA__USERNAME") or section.get("username")
    password = os.environ.get("COCO_BACKENDS__KASA__PASSWORD") or section.get("password")
    if username and password:
        return (str(username), str(password))
    return None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="make kasa-probe HOST=<ip> ARGS=...",
        description="Probe one TP-Link Kasa device (read-only unless --toggle).",
    )
    p.add_argument("--host", required=True, help="the device's IP address or hostname")
    p.add_argument("--reads", type=int, default=10, help="state reads to time (default 10)")
    p.add_argument("--timeout", type=float, default=2.0, help="seconds per call (default 2)")
    p.add_argument("--toggle", action="store_true", help="flip the device/outlet and back")
    p.add_argument("--child", help="with --toggle: one outlet, by 0-based index or alias")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            run_probe(
                args.host,
                credentials=configured_credentials(),
                reads=args.reads,
                toggle=args.toggle,
                child=args.child,
                timeout_s=args.timeout,
            )
        )
    except AuthRequired:
        print(
            "The device needs the TP-Link login (KLAP), or rejected the one configured. Set "
            "[backends.kasa] username/password in hub/config.local.toml (docs/kasa.md).",
            file=sys.stderr,
        )
        return 2
    except Unreachable as exc:
        print(
            f"No Kasa device answered at {args.host} ({exc}). Is it powered, and on the "
            "same network and subnet as this Mac?",
            file=sys.stderr,
        )
        return 2
    except (Unsupported, DeviceError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(format_report(args.host, result))
    return 0 if result.toggle is None or result.toggle.ok else 1


if __name__ == "__main__":
    sys.exit(main())
