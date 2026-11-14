"""Curate the hey-coco sound library from Wikimedia Commons.

Reads ``assets/sounds/sources.toml`` and, for every entry, downloads the chosen Commons
audio + image, trims/normalises/encodes the clip to AAC ``.m4a`` (mono, 44.1 kHz), downscales
the image to a <= 1024 px JPEG, and writes ``manifest.json`` and ``ATTRIBUTION.md``.

Usage (from the repo root; `make sounds-build` wraps the build step)::

    uv run --with httpx python assets/sounds/tools/curate.py build [--only cow,dog] [--force]
    uv run --with httpx python assets/sounds/tools/curate.py search "cow moo" "dog bark"
    uv run --with httpx python assets/sounds/tools/curate.py search --images "cow"
    uv run --with httpx python assets/sounds/tools/curate.py lead-image Cattle "Fire engine"

Needs ``ffmpeg`` (preferred, for decoding Ogg/FLAC/WAV and loudness normalisation) or
macOS ``afconvert``, plus macOS ``sips`` for images. Only licences in ``ALLOWED_LICENSES``
are accepted; anything else (NC, ND, GFDL-only, unknown) aborts the build.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from array import array
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources.toml"
CLIPS = ROOT / "clips"
IMAGES = ROOT / "images"
MANIFEST = ROOT / "manifest.json"
ATTRIBUTION = ROOT / "ATTRIBUTION.md"
CACHE = ROOT / ".cache"

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "hey-coco-sound-curation/0.1 (https://github.com/StormKiln/hey-coco)"

MAX_SECONDS = 6.0
FADE_SECONDS = 0.25
TARGET_LUFS = -16.0
TRUE_PEAK = -1.5
SAMPLE_RATE = 44100
AAC_BITRATE = "96k"
IMAGE_MAX_PX = 1024
JPEG_QUALITY = 60

# Keep in sync with hub/src/coco_hub/sounds.py ALLOWED_LICENSES.
ALLOWED_LICENSES = {
    "CC0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "Public domain": "https://en.wikipedia.org/wiki/Public_domain",
    "CC BY 1.0": "https://creativecommons.org/licenses/by/1.0/",
    "CC BY 2.0": "https://creativecommons.org/licenses/by/2.0/",
    "CC BY 2.5": "https://creativecommons.org/licenses/by/2.5/",
    "CC BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-SA 1.0": "https://creativecommons.org/licenses/by-sa/1.0/",
    "CC BY-SA 2.0": "https://creativecommons.org/licenses/by-sa/2.0/",
    "CC BY-SA 2.5": "https://creativecommons.org/licenses/by-sa/2.5/",
    "CC BY-SA 3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
}

_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
        )
    return _client


def api(**params: object) -> dict:
    params = {"action": "query", "format": "json", "formatversion": 2, **params}
    for attempt in range(5):
        r = client().get(API, params=params)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def plain(value: str | None) -> str:
    """Commons extmetadata is HTML; reduce it to one line of text."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalise_license(short_name: str) -> str | None:
    s = plain(short_name).replace("Creative Commons ", "CC ").strip()
    s = re.sub(r"(?i)^cc[- ]zero$", "CC0", s)
    s = re.sub(r"(?i)^cc0( 1\.0)?$", "CC0", s)
    s = re.sub(r"(?i)^public ?domain$|^pd$", "Public domain", s)
    s = re.sub(r"(?i)^cc[- ]by[- ]sa[- ]", "CC BY-SA ", s)
    s = re.sub(r"(?i)^cc[- ]by[- ](\d)", r"CC BY \1", s)
    return s if s in ALLOWED_LICENSES else None


_NOT_AN_AUTHOR = {"", "myself", "me", "self", "own work", "unknown", "unknown author"}


def file_info(title: str) -> dict:
    """Original URL + licence/author metadata for a ``File:`` title."""
    title = title if title.startswith("File:") else f"File:{title}"
    data = api(
        titles=title,
        prop="imageinfo",
        iiprop="url|size|mime|extmetadata|user",
        iilimit=50,
        iiextmetadatafilter="LicenseShortName|LicenseUrl|Artist|Credit|ImageDescription",
    )
    page = data["query"]["pages"][0]
    if page.get("missing") or "imageinfo" not in page:
        raise SystemExit(f"Commons file not found: {title}")
    ii = page["imageinfo"][0]
    meta = ii.get("extmetadata", {})
    raw_license = meta.get("LicenseShortName", {}).get("value", "")
    license_name = normalise_license(raw_license)
    if license_name is None:
        raise SystemExit(f"{title}: licence {plain(raw_license)!r} is not allowed")
    author = plain(meta.get("Artist", {}).get("value")) or plain(
        meta.get("Credit", {}).get("value")
    )
    if author.lower() in _NOT_AN_AUTHOR:
        # No usable Artist field: credit the original uploader, which is who Commons
        # shows as the source for "own work" uploads.
        uploader = page["imageinfo"][-1].get("user")
        author = f"Wikimedia Commons user {uploader}" if uploader else "Unknown author"
    license_url = meta.get("LicenseUrl", {}).get("value") or ALLOWED_LICENSES[license_name]
    return {
        "title": page["title"],
        "url": ii["url"],
        "size": ii.get("size", 0),
        "duration": ii.get("duration"),
        "attribution": {
            "author": author,
            "license": license_name,
            "license_url": license_url,
            "source_url": ii["descriptionurl"],
        },
    }


def download(url: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1])[-120:]
    dest = CACHE / name
    if not dest.exists() or dest.stat().st_size == 0:
        for attempt in range(5):
            r = client().get(url)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            dest.write_bytes(r.content)
            break
        else:
            raise SystemExit(f"rate limited downloading {url}")
    return dest


def run(*cmd: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed: {proc.stderr.strip()[-800:]}")
    return proc.stdout + proc.stderr


def loudest_window(src: Path, seconds: float = MAX_SECONDS) -> float:
    """Start time of the ``seconds``-long window with the most energy (ffmpeg only).

    Used when sources.toml gives no ``start`` for a long recording, so a 2-minute field
    recording yields its busiest 6 s rather than its (often quiet) opening.
    """
    rate, hop = 8000, 800  # 0.1 s frames
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "-",
        ],  # fmt: skip
        capture_output=True,
        check=True,
    ).stdout
    samples = array("h", raw[: len(raw) // 2 * 2])
    frames = [
        sum(s * s for s in samples[i : i + hop]) for i in range(0, len(samples) - hop + 1, hop)
    ]
    width = int(seconds * rate / hop)
    if len(frames) <= width:
        return 0.0
    best = current = sum(frames[:width])
    best_at = 0
    for i in range(width, len(frames)):
        current += frames[i] - frames[i - width]
        if current > best:
            best, best_at = current, i - width + 1
    return round(best_at * hop / rate, 1)


def encode_clip(
    src: Path, dest: Path, start: float | None, end: float | None
) -> tuple[float, float]:
    """Trim, fade, loudness-normalise and encode to mono 44.1 kHz AAC.

    Returns (start used, encoded duration).

    ``start`` None means "pick the loudest window" for sources longer than MAX_SECONDS.
    """
    if start is None:
        start = loudest_window(src) if shutil.which("ffmpeg") and end is None else 0.0
    end = start + MAX_SECONDS if end is None else min(end, start + MAX_SECONDS)
    length = min(end - start, max(probe_duration(src) - start, 0.1))
    if shutil.which("ffmpeg"):
        filters = (
            f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.02,afade=t=out:st={max(length - FADE_SECONDS, 0)}:d={FADE_SECONDS},"
            f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11,"
            f"aresample={SAMPLE_RATE}"
        )
        run(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-af", filters, "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "aac", "-b:a", AAC_BITRATE, "-movflags", "+faststart", str(dest),
        )  # fmt: skip
    else:
        # afconvert fallback: no trim/fade/loudness filters; only works for formats Core Audio
        # can decode (WAV/FLAC/MP3), not Ogg Vorbis/Opus.
        if start or length < MAX_SECONDS:
            print(f"  warning: afconvert cannot trim; {dest.name} uses the whole file")
        run(
            "afconvert", "-f", "m4af", "-d", f"aac@{SAMPLE_RATE}", "-c", "1",
            "-b", "96000", str(src), str(dest),
        )  # fmt: skip
    return start, probe_duration(dest)


def probe_duration(path: Path) -> float:
    if shutil.which("ffprobe"):
        out = run(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        )  # fmt: skip
        return round(float(out.strip()), 2)
    out = run("afinfo", str(path))
    match = re.search(r"estimated duration: ([\d.]+)", out)
    return round(float(match.group(1)), 2) if match else 0.0


def encode_image(src: Path, dest: Path) -> None:
    tmp = dest.with_suffix(".tmp.jpg")
    run(
        "sips", "-s", "format", "jpeg", "-s", "formatOptions", str(JPEG_QUALITY),
        "-Z", str(IMAGE_MAX_PX), str(src), "--out", str(tmp),
    )  # fmt: skip
    tmp.replace(dest)


def load_sources() -> list[dict]:
    with SOURCES.open("rb") as f:
        sounds = tomllib.load(f)["sound"]
    ids = [s["id"] for s in sounds]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate ids in sources.toml: {sorted(dupes)}")
    return sounds


def build(only: set[str] | None, force: bool) -> None:
    CLIPS.mkdir(exist_ok=True)
    IMAGES.mkdir(exist_ok=True)
    previous = {}
    if MANIFEST.exists():
        previous = {s["id"]: s for s in json.loads(MANIFEST.read_text())["sounds"]}
    entries = []
    for src in load_sources():
        sid = src["id"]
        clip = CLIPS / f"{sid}.m4a"
        image = IMAGES / f"{sid}.jpg"
        wanted = {
            "audio_file": src["audio"],
            "image_file": src["image"],
            "start": src.get("start"),
            "end": src.get("end"),
        }
        old = previous.get(sid, {})
        reuse = (
            not force
            and (only is None or sid not in only)
            and clip.exists()
            and image.exists()
            and {k: old.get("curation", {}).get(k) for k in wanted} == wanted
        )
        if reuse:
            old.update(name=src["name"], aliases=src.get("aliases", []), category=src["category"])
            entries.append(old)
            continue
        print(f"{sid}: {src['audio']}")
        audio = file_info(src["audio"])
        img = file_info(src["image"])
        start_used, duration = encode_clip(
            download(audio["url"]), clip, src.get("start"), src.get("end")
        )
        encode_image(download(img["url"]), image)
        entries.append(
            {
                "id": sid,
                "name": src["name"],
                "aliases": src.get("aliases", []),
                "category": src["category"],
                "clip": f"clips/{sid}.m4a",
                "image": f"images/{sid}.jpg",
                "duration_s": duration,
                "audio_attribution": audio["attribution"],
                "image_attribution": img["attribution"],
                "curation": {**wanted, "start_used": start_used},
            }
        )
    write_outputs(entries)


def write_outputs(entries: list[dict]) -> None:
    keep = {e["id"] for e in entries}
    for folder, ext in ((CLIPS, ".m4a"), (IMAGES, ".jpg")):
        for f in folder.glob(f"*{ext}"):
            if f.stem not in keep:
                f.unlink()
    manifest = {
        "version": 1,
        "generated_by": "assets/sounds/tools/curate.py",
        "sounds": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    ATTRIBUTION.write_text(attribution_markdown(entries))
    total = sum(f.stat().st_size for d in (CLIPS, IMAGES) for f in d.iterdir())
    print(f"{len(entries)} sounds, {total / 1e6:.1f} MB of media")


def attribution_markdown(entries: list[dict]) -> str:
    def cite(a: dict) -> str:
        return (
            f"{a['author']}, [{a['license']}](<{a['license_url']}>), "
            f"via [Wikimedia Commons](<{a['source_url']}>)"
        )

    lines = [
        "# Sound library attribution",
        "",
        "<!-- Generated by assets/sounds/tools/curate.py from manifest.json. Do not edit. -->",
        "",
        "Every clip and picture comes from Wikimedia Commons under a free licence "
        "(CC0, public domain, CC BY or CC BY-SA). Clips were trimmed, faded, "
        "loudness-normalised and re-encoded to AAC; pictures were downscaled and re-encoded "
        "to JPEG. Those adapted versions are offered under the same licence as their source.",
        "",
    ]
    for category, heading in (("animal", "Animals"), ("vehicle", "Vehicles")):
        lines += [f"## {heading}", ""]
        for e in sorted((e for e in entries if e["category"] == category), key=lambda e: e["id"]):
            lines += [
                f"### {e['name']} (`{e['id']}`)",
                "",
                f"- Sound: {cite(e['audio_attribution'])}",
                f"- Picture: {cite(e['image_attribution'])}",
                "",
            ]
    counts: dict[str, int] = {}
    for e in entries:
        for key in ("audio_attribution", "image_attribution"):
            lic = e[key]["license"]
            counts[lic] = counts.get(lic, 0) + 1
    lines += ["## Licence summary", "", "| Licence | Files |", "|---|---|"]
    lines += [f"| {lic} | {n} |" for lic, n in sorted(counts.items())]
    return "\n".join(lines) + "\n"


def search(queries: list[str], images: bool, limit: int) -> None:
    kind = "bitmap" if images else "audio"
    for q in queries:
        data = api(
            generator="search",
            gsrsearch=f"{q} filetype:{kind}",
            gsrnamespace=6,
            gsrlimit=limit,
            prop="imageinfo",
            iiprop="size|mime|extmetadata",
            iiextmetadatafilter="LicenseShortName|ImageDescription",
        )
        pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        print(f"=== {q}")
        for p in pages:
            ii = p["imageinfo"][0]
            meta = ii.get("extmetadata", {})
            lic = plain(meta.get("LicenseShortName", {}).get("value"))
            ok = "ok " if normalise_license(lic) else "NO "
            if images:
                dims = f"{ii['width']}x{ii['height']}"
            else:
                dims = f"{ii.get('duration', 0):6.1f}s"
            desc = plain(meta.get("ImageDescription", {}).get("value"))[:70]
            size_kb = ii["size"] // 1024
            print(f"  {ok}{dims:>10} {size_kb:6d}KB {lic[:16]:16} {p['title'][5:]} | {desc}")


def lead_images(articles: list[str]) -> None:
    """Print the English Wikipedia lead image of each article, with its Commons licence."""
    r = client().get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
            "titles": "|".join(articles),
            "prop": "pageimages",
            "piprop": "name",
        },
    )
    r.raise_for_status()
    for page in r.json()["query"]["pages"]:
        name = page.get("pageimage")
        if not name:
            print(f"{page['title']}: (no lead image)")
            continue
        try:
            info = file_info(name)
            lic = info["attribution"]["license"]
        except SystemExit as exc:  # not on Commons, or licence not allowed
            lic = f"REJECTED ({exc})"
        print(f"{page['title']}: File:{name.replace('_', ' ')} [{lic}]")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="download + process everything in sources.toml")
    b.add_argument("--only", help="comma-separated ids to (re)build")
    b.add_argument("--force", action="store_true", help="rebuild even if unchanged")
    s = sub.add_parser("search", help="search Commons for candidate files")
    s.add_argument("queries", nargs="+")
    s.add_argument("--images", action="store_true")
    s.add_argument("--limit", type=int, default=12)
    w = sub.add_parser("lead-image", help="show en.wikipedia lead images for articles")
    w.add_argument("articles", nargs="+")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        build(set(args.only.split(",")) if args.only else None, args.force)
    elif args.cmd == "lead-image":
        lead_images(args.articles)
    else:
        search(args.queries, args.images, args.limit)


if __name__ == "__main__":
    main(sys.argv[1:])
