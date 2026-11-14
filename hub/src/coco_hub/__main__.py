"""``python -m coco_hub [--reload]``: serve the Hub on the configured host/port."""

import argparse
import sys

import uvicorn
from pydantic import ValidationError

from coco_hub.logging_setup import configure_logging
from coco_hub.settings import config_path, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coco_hub")
    parser.add_argument("--reload", action="store_true", help="reload on code changes (dev)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"Invalid Coco Hub settings (config: {config_path()}):\n{exc}", file=sys.stderr)
        return 2

    configure_logging(settings)
    uvicorn.run(
        "coco_hub.app:create_app_from_env",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        # Uvicorn's loggers propagate to ours (JSON file + redaction) instead of its own
        # handlers. Query strings in access lines are redacted (logging_setup).
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
