"""Hub logging: JSON lines to a rotating file, human-readable lines to stderr.

``configure_logging(settings)`` runs once at startup (calling it again replaces what it set
up). Every record passes a ``RedactionFilter`` first, which removes:

- every ``SecretStr`` value in the settings (LAN token, YouTube key, Spotify secret),
- ``Authorization`` headers and ``Bearer`` credentials,
- secret-looking query parameters (``code``, ``state``, ``key``, ``token``, …) — e.g. the
  Spotify callback in uvicorn's access log, or the YouTube key in an httpx request line,
- ``access_token`` / ``refresh_token`` style values in JSON-ish text,
- values of extras whose key names a secret (``authorization``, ``api_key``, …).

Transcripts are household speech: they go only to this local file (and stderr, which launchd
also writes locally); ``diagnostics.log_transcripts = false`` keeps them out of logs entirely
(see ``coco_hub.diagnostics``).
"""

import json
import logging
import logging.handlers
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import BaseModel, SecretStr

from coco_hub.settings import Settings

REDACTED = "[REDACTED]"
LOG_FILE = "hub.jsonl"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5
HUMAN_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Loggers that would otherwise log every request URL at INFO (httpx logs full URLs, which for
# YouTube carry the API key) or are just noise.
QUIET_LOGGERS = ("httpx", "httpcore", "zeroconf")

_SECRET_KEYS = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "key",
        "client_secret",
        "code_verifier",
        "password",
        "secret",
    }
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer x / 'authorization': 'Bearer x' / "Authorization": "Basic x"
    (
        re.compile(
            r"""(?i)(authorization["']?\s*[:=]\s*["']?)(?:(?:bearer|basic)\s+)?[^\s"',;}]+"""
        ),
        rf"\g<1>{REDACTED}",
    ),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), rf"\g<1>{REDACTED}"),
    # Query parameters: ?code=…&state=…, &key=…, ?token=…
    (
        re.compile(
            r"(?i)([?&](?:code|state|key|api_key|token|access_token|refresh_token"
            r"|client_secret|code_verifier)=)[^&\s\"']+"
        ),
        rf"\g<1>{REDACTED}",
    ),
    # "access_token": "…" / access_token=… in JSON-ish text or form bodies
    (
        re.compile(
            r"""(?i)(["']?(?:access_token|refresh_token|client_secret|code_verifier|api_key)"""
            r"""["']?\s*[:=]\s*["']?)[^\s"'&,}]+"""
        ),
        rf"\g<1>{REDACTED}",
    ),
)

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "color_message"}  # uvicorn's ANSI-coloured copy of msg
)


def secret_values(settings: BaseModel) -> list[str]:
    """Every non-empty ``SecretStr`` value in ``settings`` (recursively)."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, SecretStr):
            if raw := value.get_secret_value():
                found.append(raw)
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name))

    walk(settings)
    return found


class RedactionFilter(logging.Filter):
    """Scrubs secrets from a record's message, exception text and extras (in place)."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Longest first, so a secret containing another is replaced whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def _value(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, SecretStr):
            return REDACTED
        if key is not None and key.lower() in _SECRET_KEYS and isinstance(value, str):
            return REDACTED
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self._value(v) for v in value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string shouldn't drop the record
            message = str(record.msg)
        record.msg = self.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, self._value(value, key))
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, ``logger``, ``msg``, extras, ``exc``."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC)
        line: dict[str, Any] = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in _extras(record).items():
            line.setdefault(key, value)
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line["exc"] = record.exc_text
        if record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, default=str, ensure_ascii=False)


_installed: list[logging.Handler] = []


def reset_logging() -> None:
    """Remove the handlers ``configure_logging`` added (tests; reconfiguration)."""
    root = logging.getLogger()
    while _installed:
        handler = _installed.pop()
        root.removeHandler(handler)
        handler.close()


def configure_logging(
    settings: Settings, *, stream: TextIO | None = None, level: int = logging.INFO
) -> None:
    """Log to ``<log_dir>/hub.jsonl`` (JSON lines, rotated) and ``stream`` (default stderr).

    Uvicorn must be started with ``log_config=None`` so its loggers propagate here.
    """
    reset_logging()
    redaction = RedactionFilter(secret_values(settings))

    human = logging.StreamHandler(stream if stream is not None else sys.stderr)
    human.setFormatter(logging.Formatter(HUMAN_FORMAT))
    handlers: list[logging.Handler] = [human]
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file = logging.handlers.RotatingFileHandler(
            settings.log_dir / LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file.setFormatter(JsonFormatter())
        handlers.append(file)
    except OSError as exc:  # an unwritable log dir must not stop the Hub
        print(f"coco_hub: can't write logs to {settings.log_dir}: {exc}", file=sys.stderr)

    root = logging.getLogger()
    for handler in handlers:
        handler.addFilter(redaction)
        root.addHandler(handler)
        _installed.append(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
