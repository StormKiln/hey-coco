"""``python -m coco_hub [--reload | --fingerprint | --pair]``: serve the Hub on its host/port.

With ``tls.enabled`` (the default) the Hub serves HTTPS/WSS with its self-signed certificate
from ``data_dir/tls`` (created on first run; see coco_hub.identity). ``--fingerprint`` prints
the short fingerprint to compare with the one the iPad shows when pairing. ``--pair`` issues a
one-time pairing code and prints it with a QR code for the iPad to scan (coco_hub.pairing).
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import uvicorn
from pydantic import ValidationError

from coco_hub import db, identity, pairing
from coco_hub.logging_setup import configure_logging
from coco_hub.settings import Settings, config_path, load_settings

log = logging.getLogger("coco_hub")


def server_options(settings: Settings) -> dict[str, Any]:
    """uvicorn host/port, plus the certificate and key when TLS is on."""
    options: dict[str, Any] = {"host": settings.host, "port": settings.port}
    if settings.tls.enabled:
        ident = identity.load_or_create(settings.data_dir)
        options["ssl_certfile"] = str(ident.cert_path)
        options["ssl_keyfile"] = str(ident.key_path)
    return options


def print_pairing(settings: Settings) -> int:
    """Issue a pairing code and show it, with the QR code, on this terminal only (never logged:
    the QR code holds the token)."""
    if not settings.tls.enabled:
        print("tls.enabled is false: the iPad can't pair with this Hub.", file=sys.stderr)
        return 2
    ident = identity.load_or_create(settings.data_dir)
    conn = db.open_db(settings.data_dir)
    try:
        code = pairing.issue_code(conn, now=datetime.now(UTC))
    finally:
        conn.close()
    uri = pairing.pairing_uri(
        host=pairing.host_hint(settings),
        port=settings.port,
        token=settings.token.get_secret_value(),
        cert_sha256=ident.fingerprint,
    )
    minutes = int(pairing.CODE_TTL.total_seconds() // 60)
    print("On the iPad: Pair with Coco Hub > Scan QR code, and point it at this:\n")
    print(pairing.render_qr(uri))
    print(
        f"Or tap Enter code and type:  {code[:3]} {code[3:]}   (works once, for {minutes} minutes)"
    )
    print(f"Check the iPad shows this fingerprint: {ident.short_fingerprint}")
    print("Coco Hub must be running. Run make hub-pair again for a new code.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coco_hub")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reload", action="store_true", help="reload on code changes (dev)")
    mode.add_argument(
        "--fingerprint",
        action="store_true",
        help="print the Hub ID and TLS fingerprint the iPad shows when pairing, then exit",
    )
    mode.add_argument(
        "--pair",
        action="store_true",
        help="print a QR code and a one-time 6-digit code to pair the iPad, then exit",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"Invalid Coco Hub settings (config: {config_path()}):\n{exc}", file=sys.stderr)
        return 2

    if args.fingerprint:
        ident = identity.load_or_create(settings.data_dir)
        print(f"Coco Hub fingerprint: {ident.short_fingerprint}")
        print(f"Hub ID: {ident.hub_id}")
        if not settings.tls.enabled:
            print("Warning: tls.enabled is false; the iPad can't pair with this Hub.")
        return 0

    if args.pair:
        return print_pairing(settings)

    configure_logging(settings)
    options = server_options(settings)
    if settings.tls.enabled:
        ident = identity.load_or_create(settings.data_dir)
        log.info(
            "Serving HTTPS on port %s; pairing fingerprint %s (Hub ID %s)",
            settings.port,
            ident.short_fingerprint,
            ident.hub_id,
        )
    else:
        log.warning("tls.enabled is false: serving plain HTTP; the iPad can't pair")
    uvicorn.run(
        "coco_hub.app:create_app_from_env",
        factory=True,
        reload=args.reload,
        # Uvicorn's loggers propagate to ours (JSON file + redaction) instead of its own
        # handlers. Query strings in access lines are redacted (logging_setup).
        log_config=None,
        **options,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
