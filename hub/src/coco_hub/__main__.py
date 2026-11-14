"""``python -m coco_hub [--reload | --fingerprint]``: serve the Hub on the configured host/port.

With ``tls.enabled`` (the default) the Hub serves HTTPS/WSS with its self-signed certificate
from ``data_dir/tls`` (created on first run; see coco_hub.identity). ``--fingerprint`` prints
the short fingerprint to compare with the one the iPad shows when pairing.
"""

import argparse
import logging
import sys
from typing import Any

import uvicorn
from pydantic import ValidationError

from coco_hub import identity
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coco_hub")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reload", action="store_true", help="reload on code changes (dev)")
    mode.add_argument(
        "--fingerprint",
        action="store_true",
        help="print the Hub ID and TLS fingerprint the iPad shows when pairing, then exit",
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
