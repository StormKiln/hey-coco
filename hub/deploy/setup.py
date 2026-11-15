"""``make hub-setup``: bootstrap the always-on Mac for Coco Hub. Safe to run again.

1. uv and Ollama: if missing, offers ``brew install`` (asks; the default is to print the
   command, never to run it). Then offers ``brew services start ollama`` and
   ``ollama pull <qwen.model>`` the same way.
2. ``hub/config.local.toml``: if missing, writes it from ``config.example.toml`` with a fresh
   token, your home lat/lon and timezone, and Qwen on. An existing file is never touched.
3. Prints the Hub's TLS fingerprint and the next steps.

Non-interactive use (no prompts; brew/ollama commands are only printed)::

    make hub-setup ARGS="--non-interactive --lat 45.42 --lon -75.69 --timezone America/Toronto"

or set ``COCO_SETUP_LAT`` / ``COCO_SETUP_LON`` / ``COCO_SETUP_TIMEZONE``.

Standard library only, and runs on the Mac's stock ``python3``: uv may not be installed yet.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HUB = Path(__file__).resolve().parents[1]
CONFIG = HUB / "config.local.toml"
EXAMPLE = HUB / "config.example.toml"
DEFAULT_MODEL = "qwen3:8b"

Ask = Callable[[str], str]
Say = Callable[[str], None]


class SetupError(Exception):
    """A value the user gave can't be used, or a required one is missing."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""


class Runner:
    """Runs commands for real. Tests pass a fake with the same two methods."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, cmd: list[str], cwd: Path | None = None) -> Result:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if proc.returncode != 0 and proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        return Result(proc.returncode, proc.stdout)


# --- config ------------------------------------------------------------------------------------


def new_token() -> str:
    return secrets.token_urlsafe(32)


def parse_location(lat: str, lon: str, timezone: str) -> tuple[float, float, str]:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except ValueError:
        raise SetupError(f"latitude and longitude must be numbers, not {lat!r}, {lon!r}") from None
    if not -90 <= lat_f <= 90:
        raise SetupError(f"latitude must be between -90 and 90, not {lat_f}")
    if not -180 <= lon_f <= 180:
        raise SetupError(f"longitude must be between -180 and 180, not {lon_f}")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise SetupError(f"unknown timezone {timezone!r} (e.g. America/Toronto)") from None
    return lat_f, lon_f, timezone


def _sections(text: str) -> list[tuple[str, str]]:
    """``(section, line)`` for each line; top-level lines have section ``""``."""
    section = ""
    out = []
    for line in text.splitlines(keepends=True):
        header = re.match(r"\s*\[([^\]]+)\]", line)
        if header:
            section = header.group(1).strip()
        out.append((section, line))
    return out


def _set(text: str, section: str, key: str, value: str) -> str:
    """Replace ``key = …`` (keeping any trailing comment) in ``section``; it must exist."""
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*(?:\"[^\"]*\"|[^#\s]+)(.*)$", re.DOTALL)
    found = False
    lines = []
    for sec, line in _sections(text):
        m = pattern.match(line) if sec == section and not found else None
        if m:
            line = f"{m.group(1)}{key} = {value}{m.group(2)}"
            found = True
        lines.append(line)
    if not found:
        raise SetupError(f"config example has no {key!r} in [{section or 'top level'}]")
    return "".join(lines)


def render_config(example: str, *, token: str, lat: float, lon: float, timezone: str) -> str:
    """The example config with the token, home location and timezone filled in, Qwen on."""
    text = _set(example, "", "token", f'"{token}"')
    text = _set(text, "", "timezone", f'"{timezone}"')
    text = _set(text, "location", "lat", repr(float(lat)))
    text = _set(text, "location", "lon", repr(float(lon)))
    return _set(text, "qwen", "enabled", "true")


def _value(text: str, section: str, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*\"([^\"]*)\"")
    for sec, line in _sections(text):
        if sec == section and (m := pattern.match(line)):
            return m.group(1)
    return None


def qwen_model(config_text: str) -> str:
    return _value(config_text, "qwen", "model") or DEFAULT_MODEL


def _prompt_until_valid(ask: Ask, say: Say, question: str, check: Callable[[str], object]) -> str:
    while True:
        answer = ask(question).strip()
        try:
            check(answer)
        except SetupError as exc:
            say(f"  {exc}")
            continue
        return answer


def write_config(
    config: Path,
    example: Path,
    *,
    lat: str | None,
    lon: str | None,
    timezone: str | None,
    ask: Ask,
    interactive: bool,
    say: Say,
) -> bool:
    """Create ``config`` from ``example`` if it doesn't exist. True if it was written."""
    if config.exists():
        say(f"{config} already exists; leaving it as it is.")
        return False
    example_text = example.read_text()
    if not interactive:
        missing = [
            f"--{n}" for n, v in (("lat", lat), ("lon", lon), ("timezone", timezone)) if not v
        ]
        if missing:
            raise SetupError(
                f"{', '.join(missing)} needed to write {config.name} (or run interactively)"
            )
    say("Home location, for the weather (right-click your house in Google Maps to copy it).")
    if lat is None:
        lat = _prompt_until_valid(ask, say, "  Latitude: ", lambda v: parse_location(v, "0", "UTC"))
    if lon is None:
        lon = _prompt_until_valid(
            ask, say, "  Longitude: ", lambda v: parse_location("0", v, "UTC")
        )
    if timezone is None:
        default = _value(example_text, "", "timezone") or "UTC"
        timezone = (
            _prompt_until_valid(
                ask,
                say,
                f"  Timezone [{default}]: ",
                lambda v: parse_location("0", "0", v or default),
            )
            or default
        )
    lat_f, lon_f, tz = parse_location(lat, lon, timezone)
    text = render_config(example_text, token=new_token(), lat=lat_f, lon=lon_f, timezone=tz)
    # Created owner-only before the token is written into it.
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    say(f"Wrote {config} with a new token (type it on the iPad when pairing).")
    return True


# --- tools ---------------------------------------------------------------------------------------


def _offer(
    cmd: list[str], *, why: str, runner: Runner, ask: Ask, interactive: bool, say: Say
) -> bool:
    """Ask before running ``cmd``; the default (and non-interactive) is to print it. True if run."""
    line = " ".join(cmd)
    if interactive and ask(f"{why} Run `{line}` now? [y/N] ").strip().lower() in ("y", "yes"):
        say(f"$ {line}")
        ok = runner.run(cmd).returncode == 0
        if not ok:
            say(f"`{line}` failed; run it yourself to see why.")
        return ok
    say(f"{why} Run: {line}")
    return False


def ensure_tool(name: str, *, runner: Runner, ask: Ask, interactive: bool, say: Say) -> bool:
    """True if ``name`` is (now) on PATH."""
    if runner.which(name):
        say(f"{name}: found")
        return True
    if not runner.which("brew"):
        say(f"{name}: not found. Install Homebrew (https://brew.sh), then: brew install {name}")
        return False
    return _offer(
        ["brew", "install", name],
        why=f"{name}: not found.",
        runner=runner,
        ask=ask,
        interactive=interactive,
        say=say,
    )


def ensure_ollama(model: str, *, runner: Runner, ask: Ask, interactive: bool, say: Say) -> None:
    """Offer to start Ollama as a login service and to pull ``model`` if it isn't there."""
    listing = runner.run(["ollama", "list"])
    pulled = listing.returncode == 0 and any(
        line.split()[0] == model for line in listing.stdout.splitlines()[1:] if line.split()
    )
    if runner.which("brew"):
        _offer(
            ["brew", "services", "start", "ollama"],
            why="Ollama should run as a background service that survives reboots.",
            runner=runner,
            ask=ask,
            interactive=interactive,
            say=say,
        )
    if pulled:
        say(f"ollama: {model} is pulled")
        return
    _offer(
        ["ollama", "pull", model],
        why=f"ollama: {model} is not pulled yet (several GB).",
        runner=runner,
        ask=ask,
        interactive=interactive,
        say=say,
    )


def print_fingerprint(*, runner: Runner, say: Say) -> None:
    if not runner.which("uv"):
        say("Fingerprint: install uv, then run `make hub-fingerprint`.")
        return
    result = runner.run(
        ["uv", "run", "--quiet", "python", "-m", "coco_hub", "--fingerprint"], cwd=HUB
    )
    if result.returncode != 0:
        say("Couldn't read the fingerprint; run `make hub-fingerprint` to see why.")
        return
    say(result.stdout.strip())


# --- main ------------------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    env = os.environ
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lat", default=env.get("COCO_SETUP_LAT"), help="home latitude")
    parser.add_argument("--lon", default=env.get("COCO_SETUP_LON"), help="home longitude")
    parser.add_argument(
        "--timezone",
        default=env.get("COCO_SETUP_TIMEZONE"),
        help="IANA timezone, e.g. America/Toronto",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt: config values from flags/env; brew/ollama commands are only printed",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    ask: Ask = input,
    say: Say = print,
    config: Path = CONFIG,
    example: Path = EXAMPLE,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    runner = runner or Runner()
    interactive = not args.non_interactive and (ask is not input or sys.stdin.isatty())
    common = {"runner": runner, "ask": ask, "interactive": interactive, "say": say}

    say("== Tools")
    ensure_tool("uv", **common)
    have_ollama = ensure_tool("ollama", **common)

    say("== Settings")
    try:
        write_config(
            config,
            example,
            lat=args.lat,
            lon=args.lon,
            timezone=args.timezone,
            ask=ask,
            interactive=interactive,
            say=say,
        )
    except SetupError as exc:
        say(f"Error: {exc}")
        return 2
    model = qwen_model(config.read_text())

    if have_ollama:
        say("== Ollama")
        ensure_ollama(model, **common)

    say("== Fingerprint (compare it with the iPad when pairing)")
    print_fingerprint(runner=runner, say=say)

    say("== Next")
    say("  make hub-install-service   # start the Hub now and at every login")
    say("  make hub-logs              # if it doesn't come up")
    say("  Then set up the iPad: docs/kitchen-setup.md, part 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
