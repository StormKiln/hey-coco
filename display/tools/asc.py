#!/usr/bin/env python3
"""Read-only App Store Connect checks for TestFlight uploads (docs/testflight.md).

    asc.py preflight --bundle-id ID --version 0.1.0 --build 123
        Refuse (exit 1) unless HEAD is on origin/main's history and BUILD is greater than every
        build App Store Connect already has for this marketing version. Run before any signing.
    asc.py status --bundle-id ID --version 0.1.0 --build 123 [--wait-minutes 20]
        Poll Apple's processing of one uploaded build. Exit 0 when VALID, 1 when Apple marks it
        FAILED or INVALID (or the query fails), 2 (--pending-exit-code) when it is still
        processing after the wait.

Credentials come from env ASC_KEY_ID, ASC_ISSUER_ID and ASC_KEY_PATH (the AuthKey_<id>.p8 file),
the same three the upload uses. Nothing here writes to App Store Connect.

Ported from NextRound's scripts/testflight-status.mjs. Python's standard library has no ECDSA,
so signing imports `cryptography` (only when a token is actually needed); the Makefile runs this
with the Hub's locked environment, which already depends on it. Everything else is stdlib, and
the pure parts (JWT claims, build-number comparison, response parsing) are unit tested in
test_asc.py with recorded responses: no test calls Apple.

A failed query is an error, never a pass: "could not check" is not "no builds found".
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

API = "https://api.appstoreconnect.apple.com/v1/"
AUDIENCE = "appstoreconnect-v1"
# Apple rejects tokens that live longer than 20 minutes.
TOKEN_LIFETIME = 1200
POLL_SECONDS = 15
REQUEST_TIMEOUT = 30

VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
BUILD_RE = re.compile(r"^\d+(\.\d+){0,2}$")
BUNDLE_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


class AscError(Exception):
    """A check failed or could not be completed. The message is shown as is."""


# ---- JWT (ES256) ----


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def jwt_header(key_id: str) -> dict:
    return {"alg": "ES256", "kid": key_id, "typ": "JWT"}


def jwt_claims(issuer_id: str, now: int) -> dict:
    return {"iss": issuer_id, "iat": now, "exp": now + TOKEN_LIFETIME, "aud": AUDIENCE}


def signing_input(key_id: str, issuer_id: str, now: int) -> bytes:
    def enc(value: dict) -> str:
        return b64url(json.dumps(value, separators=(",", ":")).encode())

    return f"{enc(jwt_header(key_id))}.{enc(jwt_claims(issuer_id, now))}".encode("ascii")


def der_to_raw(r: int, s: int) -> bytes:
    """JWS wants ES256 signatures as r || s, 32 bytes each, not the DER that OpenSSL returns."""
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def make_token(key_id: str, issuer_id: str, key_pem: bytes, now: int | None = None) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise AscError("ASC_KEY_PATH is not an App Store Connect (P-256) private key")
    body = signing_input(key_id, issuer_id, int(time.time()) if now is None else now)
    r, s = decode_dss_signature(key.sign(body, ec.ECDSA(hashes.SHA256())))
    return f"{body.decode('ascii')}.{b64url(der_to_raw(r, s))}"


# ---- Build numbers ----


def build_key(build: str) -> tuple[int, int, int]:
    if not BUILD_RE.match(build):
        raise ValueError(f"not a build number: {build!r}")
    parts = [int(p) for p in build.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def highest_build(builds: Iterable[str]) -> str | None:
    """The highest numeric build number. Apple's build strings are digits and dots; anything
    else cannot collide with ours and is ignored."""
    numeric = [b for b in builds if BUILD_RE.match(b)]
    return max(numeric, key=build_key) if numeric else None


def check_build_number(build: str, highest: str | None, version: str) -> None:
    if highest is not None and build_key(build) <= build_key(highest):
        raise AscError(
            f"Build {build} is not greater than build {highest}, which App Store Connect already "
            f"has for version {version}. Apple refuses a build number it has seen. Upload from an "
            f"up-to-date main (the build number is the commit count), or pass a higher one: "
            f"BUILD_NUMBER=<n> where n > {highest}."
        )


def builds_in(pages: Iterable[dict]) -> list[str]:
    """Build numbers (attributes.version) from pages of GET /v1/builds."""
    return [item["attributes"]["version"] for page in pages for item in page.get("data", [])]


# ---- Processing status ----

DONE_OK = {"VALID"}
DONE_BAD = {"FAILED", "INVALID"}


def processing_state(payload: dict, version: str, build: str) -> str:
    """The processing state of exactly this build, from GET /v1/builds?include=preReleaseVersion.

    NOT_VISIBLE_YET when Apple has not listed it yet (normal for a few minutes after upload).
    """
    data = payload.get("data", [])
    if len(data) > 1:
        raise AscError(f"App Store Connect returned {len(data)} builds for {version} ({build})")
    if not data:
        return "NOT_VISIBLE_YET"
    item = data[0]
    pre_id = (item.get("relationships", {}).get("preReleaseVersion", {}).get("data") or {}).get(
        "id"
    )
    pre = next(
        (
            inc
            for inc in payload.get("included", [])
            if inc.get("type") == "preReleaseVersions" and inc.get("id") == pre_id
        ),
        None,
    )
    got_build = item["attributes"].get("version")
    got_version = (pre or {}).get("attributes", {}).get("version")
    if got_build != build or got_version != version:
        raise AscError(
            f"App Store Connect returned build {got_version} ({got_build}), "
            f"not the requested {version} ({build})"
        )
    return item["attributes"].get("processingState") or "UNKNOWN"


def poll(
    fetch: Callable[[], dict],
    version: str,
    build: str,
    attempts: int,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> str | None:
    """Poll until VALID (returns it), FAILED/INVALID (raises) or attempts run out (None)."""
    previous = None
    for attempt in range(attempts):
        state = processing_state(fetch(), version, build)
        if state != previous:
            log(f"Build processing: {state}")
        previous = state
        if state in DONE_OK:
            return state
        if state in DONE_BAD:
            raise AscError(
                f"Apple marked build {version} ({build}) {state}. Check the email Apple sent."
            )
        if attempt + 1 < attempts:
            sleep(POLL_SECONDS)
    return None


# ---- Inputs ----


def validate(bundle_id: str, version: str, build: str) -> None:
    if not BUNDLE_RE.match(bundle_id):
        raise AscError(f"Not a bundle ID: {bundle_id!r}")
    if not VERSION_RE.match(version):
        raise AscError(f"Not a marketing version (x.y.z): {version!r}")
    if not BUILD_RE.match(build):
        raise AscError(f"Not a build number: {build!r}")


def credentials() -> tuple[str, str, bytes]:
    names = ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH")
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise AscError(f"Missing env: {', '.join(missing)} (docs/testflight.md)")
    path = os.path.expanduser(os.environ["ASC_KEY_PATH"])
    try:
        with open(path, "rb") as f:
            pem = f.read()
    except OSError as e:
        raise AscError(f"Cannot read ASC_KEY_PATH: {e.strerror}") from None
    return os.environ["ASC_KEY_ID"], os.environ["ASC_ISSUER_ID"], pem


# ---- HTTP ----


class Client:
    def __init__(self, key_id: str, issuer_id: str, pem: bytes):
        self._creds = (key_id, issuer_id, pem)

    def get_url(self, url: str) -> dict:
        # A fresh token per request: status polling outlives one token's 20 minutes.
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {make_token(*self._creds)}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raise AscError(
                f"App Store Connect request failed: HTTP {e.code} for {e.url.split('?')[0]}"
            ) from None
        except (urllib.error.URLError, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            raise AscError(f"App Store Connect unreachable: {reason}") from None

    def get(self, path: str, params: dict[str, str]) -> dict:
        return self.get_url(f"{API}{path}?{urllib.parse.urlencode(params)}")

    def pages(self, path: str, params: dict[str, str], limit: int = 50) -> Iterable[dict]:
        page = self.get(path, params)
        for _ in range(limit):
            yield page
            nxt = page.get("links", {}).get("next")
            if not nxt:
                return
            page = self.get_url(nxt)
        raise AscError(f"More than {limit} pages of {path}; refusing to guess")

    def app_id(self, bundle_id: str) -> str:
        apps = self.get("apps", {"filter[bundleId]": bundle_id, "limit": "2"})["data"]
        if len(apps) != 1:
            raise AscError(
                f"Expected one App Store Connect app with bundle ID {bundle_id}, "
                f"found {len(apps)}. Create the app record first (docs/testflight.md, step 3)."
            )
        return apps[0]["id"]


# ---- Commands ----


def check_on_main(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    result = run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], capture_output=True)
    if result.returncode == 1:
        raise AscError(
            "HEAD is not on origin/main's history. TestFlight builds come from main: merge first, "
            "then `git fetch origin main` and check out main."
        )
    if result.returncode != 0:
        raise AscError(
            "Could not compare HEAD with origin/main (git fetch origin main, and a full clone)"
        )


def preflight(args: argparse.Namespace) -> int:
    check_on_main()
    print("HEAD is on origin/main.")
    client = Client(*credentials())
    app = client.app_id(args.bundle_id)
    pages = client.pages(
        "builds",
        {"filter[app]": app, "filter[preReleaseVersion.version]": args.version, "limit": "200"},
    )
    highest = highest_build(builds_in(pages))
    print(f"Highest build App Store Connect has for {args.version}: {highest or 'none'}")
    print(f"This build: {args.build}")
    check_build_number(args.build, highest, args.version)
    print("App Store Connect preflight passed.")
    return 0


def status(args: argparse.Namespace) -> int:
    if not 1 <= args.wait_minutes <= 60:
        raise AscError("--wait-minutes must be 1 to 60")
    client = Client(*credentials())
    app = client.app_id(args.bundle_id)
    params = {
        "filter[app]": app,
        "filter[version]": args.build,
        "filter[preReleaseVersion.version]": args.version,
        "include": "preReleaseVersion",
    }
    print(f"Waiting up to {args.wait_minutes} min for {args.version} ({args.build}) to process.")
    state = poll(
        lambda: client.get("builds", params),
        args.version,
        args.build,
        attempts=args.wait_minutes * 60 // POLL_SECONDS,
        sleep=time.sleep,
        log=print,
    )
    if state:
        print(f"Build {args.version} ({args.build}) is VALID: TestFlight can offer it.")
        return 0
    print(
        f"Still processing after {args.wait_minutes} min. Check again later with "
        f"`make testflight-status VERSION={args.version} BUILD={args.build}` or the "
        "TestFlight status workflow. Do not re-upload just to check.",
        file=sys.stderr,
    )
    return args.pending_exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "status"):
        p = sub.add_parser(name)
        p.add_argument("--bundle-id", required=True)
        p.add_argument("--version", required=True)
        p.add_argument("--build", required=True)
        if name == "status":
            p.add_argument("--wait-minutes", type=int, default=20)
            # Still processing is not a failure Apple reported; CI passes 0 after its upload.
            p.add_argument("--pending-exit-code", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        validate(args.bundle_id, args.version, args.build)
        return preflight(args) if args.command == "preflight" else status(args)
    except AscError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
