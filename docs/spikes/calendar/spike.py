"""Spike #138: are Google's secret iCal (ICS) addresses good enough for Coco?

Throwaway: it lives outside the Hub package (like ``docs/spikes/qwen/spike.py``) and is run by
``make calendar-spike ARGS=...``. Three modes:

``--selftest``
    Parse the fictional ``sample.ics`` next to this file with icalendar + recurring-ical-events
    + x-wr-timezone and check recurring-with-exceptions, all-day, multi-day, cross-timezone,
    daylight-saving and cancelled events against the expected Hub-local times.

``--check``
    For each ``[[calendars]]`` entry in the Hub config (``$COCO_CONFIG``, default
    ``hub/config.local.toml``): fetch it, report the caching headers, whether a conditional
    request gets a 304, the parse time and *counts* of the features the feed uses.
    Calendars are named "calendar 1, 2, ..." only. Never prints a URL, a calendar name or an
    event title.

``--watch MARKER``
    Poll every calendar every ``--interval`` seconds and report each change to events whose
    title contains MARKER (case-insensitive): when the change was first seen in the feed, the
    event's ``LAST-MODIFIED`` (Google stamps the edit time), and the lag between them. Only
    the MARKER events' titles are printed (the owner names the test event). Every record is
    appended to a JSON-lines file outside git.

Secret ICS URLs are bearer secrets: they are only ever passed to httpx, never printed, and
error messages from httpx (which include the URL) are reduced to the exception type.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import tomllib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
import x_wr_timezone

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT_CONFIG = REPO / "hub" / "config.local.toml"
DEFAULT_OUT_DIR = Path.home() / "Library" / "Application Support" / "CocoHub" / "spikes"
USER_AGENT = "coco-hub/0.1 (+https://github.com/StormKiln/hey-coco)"


# --- shared --------------------------------------------------------------------------------


def load_config() -> tuple[list[str], str]:
    path = Path(os.environ.get("COCO_CONFIG") or DEFAULT_CONFIG).expanduser()
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    urls = [c["url"] for c in cfg.get("calendars", []) if c.get("url")]
    return urls, cfg.get("timezone", "UTC")


def expand(raw: bytes, start: datetime, end: datetime) -> list[icalendar.Event]:
    cal = x_wr_timezone.to_standard(icalendar.Calendar.from_ical(raw))
    events = recurring_ical_events.of(cal).between(start, end)
    return [e for e in events if str(e.get("STATUS", "")).upper() != "CANCELLED"]


def local(value: date | datetime, tz: ZoneInfo) -> date | datetime:
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    return value


def safe_error(exc: Exception) -> str:
    """httpx messages contain the URL: keep the type (and HTTP status) only."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


# --- selftest ------------------------------------------------------------------------------

# (title, Hub-local start) for every non-cancelled instance in the sample, Hub tz Chicago.
EXPECTED = sorted(
    [
        ("Soccer practice", "2026-11-24T18:00"),
        ("Soccer practice (field 2)", "2026-12-08T18:30"),  # moved + retitled occurrence
        ("School holiday", "2026-11-27"),
        ("Camping trip", "2026-12-04"),  # all-day, 3 days (DTEND exclusive)
        ("Call with Grandma", "2026-11-28T09:00"),  # 15:00 London
        ("Dentist", "2026-11-25T14:00"),  # UTC in the feed
        ("Sleepover", "2026-11-28T19:00"),  # overnight
    ]
    # Daily at 07:30 local across the end of DST on 2026-11-01, written in UTC as Google does.
    + [("Morning swim", f"2026-{d}T07:30") for d in
       ("10-28", "10-29", "10-30", "10-31", "11-01", "11-02", "11-03", "11-04")]
)


def selftest() -> int:
    tz = ZoneInfo("America/Chicago")
    raw = (HERE / "sample.ics").read_bytes()
    start = datetime(2026, 10, 26, tzinfo=tz)
    end = datetime(2026, 12, 31, tzinfo=tz)
    t0 = time.perf_counter()
    events = expand(raw, start, end)
    ms = (time.perf_counter() - t0) * 1000
    got = sorted(
        (str(e["SUMMARY"]), local(e["DTSTART"].dt, tz).isoformat()[:16]) for e in events
    )
    ok = got == EXPECTED
    camping = next(e for e in events if str(e["SUMMARY"]) == "Camping trip")
    days = (camping["DTEND"].dt - camping["DTSTART"].dt).days
    private = [str(e["SUMMARY"]) for e in events if str(e.get("CLASS", "")).upper() == "PRIVATE"]
    print(f"parsed {len(events)} instances in {ms:.1f} ms")
    print(f"camping trip spans {days} days (expect 3); private: {private} (expect ['Dentist'])")
    if not ok:
        print("MISMATCH")
        for row in sorted(set(got) ^ set(EXPECTED)):
            print("  got " if row in got else "  want", row)
    ok = ok and days == 3 and private == ["Dentist"]
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --- check ---------------------------------------------------------------------------------


def check() -> int:
    urls, tzname = load_config()
    if not urls:
        print("no [[calendars]] with a url in the config")
        return 1
    tz = ZoneInfo(tzname)
    now = datetime.now(tz)
    start, end = now - timedelta(days=1), now + timedelta(days=14)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as http:
        for i, url in enumerate(urls, 1):
            print(f"--- calendar {i}")
            try:
                t0 = time.perf_counter()
                r = http.get(url)
                r.raise_for_status()
                fetch_ms = (time.perf_counter() - t0) * 1000
            except Exception as exc:  # report and carry on
                print("  fetch failed:", safe_error(exc))
                continue
            h = r.headers
            print(f"  {r.status_code} in {fetch_ms:.0f} ms, {len(r.content) / 1024:.0f} KiB")
            print(f"  content-type: {h.get('content-type')}")
            print(f"  cache-control: {h.get('cache-control')}  expires: {h.get('expires')}")
            print(f"  etag: {'yes' if h.get('etag') else 'no'}  "
                  f"last-modified: {h.get('last-modified') or 'no'}")
            cond = {}
            if h.get("etag"):
                cond["If-None-Match"] = h["etag"]
            if h.get("last-modified"):
                cond["If-Modified-Since"] = h["last-modified"]
            if cond:
                try:
                    r2 = http.get(url, headers=cond)
                    print(f"  conditional request -> {r2.status_code}")
                except Exception as exc:
                    print("  conditional request failed:", safe_error(exc))
            cal = icalendar.Calendar.from_ical(r.content)
            comps = list(cal.walk("VEVENT"))
            feats: Counter[str] = Counter()
            tzids: set[str] = set()
            for c in comps:
                ds = c.get("DTSTART")
                if ds is None:
                    continue
                if not isinstance(ds.dt, datetime):
                    feats["all-day"] += 1
                    if (c.get("DTEND") and (c["DTEND"].dt - ds.dt).days > 1):
                        feats["multi-day all-day"] += 1
                elif ds.dt.tzinfo is None:
                    feats["floating time"] += 1
                elif "TZID" in ds.params:
                    tzids.add(str(ds.params["TZID"]))
                else:
                    feats["UTC time"] += 1
                for key, name in (("RRULE", "RRULE"), ("EXDATE", "EXDATE"),
                                  ("RECURRENCE-ID", "RECURRENCE-ID"), ("RDATE", "RDATE")):
                    if key in c:
                        feats[name] += 1
                if str(c.get("STATUS", "")).upper() == "CANCELLED":
                    feats["STATUS:CANCELLED"] += 1
                cls = str(c.get("CLASS", "")).upper()
                if cls in ("PRIVATE", "CONFIDENTIAL"):
                    feats[f"CLASS:{cls}"] += 1
                if "LAST-MODIFIED" in c:
                    feats["has LAST-MODIFIED"] += 1
            print(f"  X-WR-TIMEZONE: {'yes' if cal.get('X-WR-TIMEZONE') else 'no'}  "
                  f"X-WR-CALNAME: {'yes' if cal.get('X-WR-CALNAME') else 'no'}  "
                  f"VTIMEZONEs: {len(list(cal.walk('VTIMEZONE')))}")
            print(f"  VEVENT components: {len(comps)}; TZIDs used: {len(tzids)}")
            for k, v in sorted(feats.items()):
                print(f"    {k}: {v}")
            t0 = time.perf_counter()
            instances = expand(r.content, start, end)
            print(f"  -1d..+14d window: {len(instances)} instances, "
                  f"parse+expand {(time.perf_counter() - t0) * 1000:.0f} ms")
    return 0


# --- watch ---------------------------------------------------------------------------------


def _fingerprint(c: icalendar.Event) -> str:
    parts = [str(c.get(k).to_ical() if hasattr(c.get(k), "to_ical") else c.get(k))
             for k in ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "STATUS", "SEQUENCE")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def watch(marker: str, interval: float, out: Path | None) -> int:
    urls, tzname = load_config()
    if not urls:
        print("no [[calendars]] with a url in the config")
        return 1
    tz = ZoneInfo(tzname)
    out = out or DEFAULT_OUT_DIR / f"calendar-lag-{datetime.now():%Y%m%d-%H%M}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"watching {len(urls)} calendar(s) for events titled *{marker}* every {interval:.0f}s")
    print(f"log: {out}  (Ctrl-C to stop)")
    seen: dict[tuple[int, str], str] = {}
    validators: dict[int, dict[str, str]] = {}
    first_pass = True
    needle = marker.casefold()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20,
                      follow_redirects=True) as http, out.open("a") as log:
        def record(**fields: object) -> None:
            fields["ts"] = datetime.now(UTC).isoformat()
            log.write(json.dumps(fields, default=str) + "\n")
            log.flush()

        while True:
            for i, url in enumerate(urls, 1):
                try:
                    r = http.get(url, headers=validators.get(i, {}))
                    if r.status_code == 304:
                        record(cal=i, status=304)
                        continue
                    r.raise_for_status()
                except Exception as exc:
                    print(f"{datetime.now(tz):%H:%M:%S} calendar {i}: {safe_error(exc)}")
                    record(cal=i, error=safe_error(exc))
                    continue
                v = {}
                if r.headers.get("etag"):
                    v["If-None-Match"] = r.headers["etag"]
                if r.headers.get("last-modified"):
                    v["If-Modified-Since"] = r.headers["last-modified"]
                validators[i] = v
                record(cal=i, status=r.status_code, last_modified_hdr=r.headers.get("last-modified"))
                now = datetime.now(UTC)
                current: set[tuple[int, str]] = set()
                for c in icalendar.Calendar.from_ical(r.content).walk("VEVENT"):
                    title = str(c.get("SUMMARY", ""))
                    if needle not in title.casefold():
                        continue
                    rid = c.get("RECURRENCE-ID")
                    key = (i, f"{c.get('UID')}|{rid.to_ical().decode() if rid else ''}")
                    current.add(key)
                    fp = _fingerprint(c)
                    if seen.get(key) == fp:
                        continue
                    lm = c.get("LAST-MODIFIED")
                    lm_dt = lm.dt if lm else None
                    lag = (now - lm_dt).total_seconds() if lm_dt and not first_pass else None
                    what = "present" if first_pass else ("new" if key not in seen else "changed")
                    start = local(c["DTSTART"].dt, tz) if c.get("DTSTART") else None
                    print(f"{datetime.now(tz):%H:%M:%S} calendar {i}: {what} {title!r} "
                          f"start={start} last-modified={lm_dt}"
                          + (f"  LAG={lag / 60:.1f} min" if lag is not None else ""))
                    record(cal=i, event=what, title=title, start=start,
                           last_modified=lm_dt, lag_s=lag)
                    seen[key] = fp
                for key in [k for k in seen if k[0] == i and k not in current]:
                    print(f"{datetime.now(tz):%H:%M:%S} calendar {i}: removed (deleted or "
                          f"renamed) at {now:%H:%M:%S}Z")
                    record(cal=i, event="removed")
                    del seen[key]
            first_pass = False
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--watch", metavar="MARKER")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.check:
        return check()
    try:
        return watch(a.watch, a.interval, a.out)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
