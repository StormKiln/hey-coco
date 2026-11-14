import io
import json
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr

from coco_hub.logging_setup import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    reset_logging,
    secret_values,
)

from conftest import TEST_TOKEN

YT_KEY = "AIzaSyFakeYoutubeKey0123456789"
SPOTIFY_SECRET = "spotify-client-secret-abcdef"


@pytest.fixture
def secret_settings(settings, tmp_path):
    return settings.model_copy(
        update={
            "log_dir": tmp_path / "logs",
            "keys": settings.keys.model_copy(update={"youtube": SecretStr(YT_KEY)}),
            "spotify": settings.spotify.model_copy(
                update={"client_secret": SecretStr(SPOTIFY_SECRET)}
            ),
        }
    )


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    reset_logging()


def record(msg: str, *args, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("coco_hub.test", logging.INFO, __file__, 1, msg, args or None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def redacted(rec: logging.LogRecord, secrets=(TEST_TOKEN,)) -> logging.LogRecord:
    assert RedactionFilter(secrets).filter(rec)
    return rec


def test_secret_values_walks_every_secret_str(secret_settings):
    assert set(secret_values(secret_settings)) == {TEST_TOKEN, YT_KEY, SPOTIFY_SECRET}


def test_redacts_known_secrets_in_message_and_args():
    rec = redacted(record("token is %s, again %s", TEST_TOKEN, TEST_TOKEN))
    assert rec.getMessage() == f"token is {REDACTED}, again {REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc.def-ghi",
        "headers={'authorization': 'Bearer abc.def-ghi'}",
        '{"Authorization": "Bearer abc.def-ghi"}',
        "sent bearer abc.def-ghi upstream",
    ],
)
def test_redacts_authorization_headers_and_bearer_tokens(text):
    message = redacted(record("%s", text), secrets=()).getMessage()
    assert "abc.def-ghi" not in message
    assert REDACTED in message


@pytest.mark.parametrize(
    "text, leaked",
    [
        ('127.0.0.1:5 - "GET /spotify/callback?code=AQCode123&state=s1 HTTP/1.1" 200', "AQCode"),
        ("HTTP Request: GET https://www.googleapis.com/youtube/v3/search?q=x&key=K123", "K123"),
        ('{"access_token": "BQAccess", "refresh_token": "AQRefresh"}', "BQAccess"),
        ('{"access_token": "BQAccess", "refresh_token": "AQRefresh"}', "AQRefresh"),
        ("/v1/events?token=sneaky-token", "sneaky-token"),
    ],
)
def test_redacts_secrets_in_query_strings_and_token_payloads(text, leaked):
    message = redacted(record("%s", text), secrets=()).getMessage()
    assert leaked not in message


def test_redacts_extras_by_key_and_value():
    rec = redacted(
        record(
            "x",
            trace={"text": f"say {TEST_TOKEN}", "headers": {"Authorization": "Bearer zzz"}},
            api_key=SecretStr("hidden"),
        )
    )
    assert rec.trace == {"text": f"say {REDACTED}", "headers": {"Authorization": REDACTED}}
    assert rec.api_key == REDACTED


def test_redacts_exception_text():
    try:
        raise RuntimeError(f"boom with {TEST_TOKEN}")
    except RuntimeError:
        import sys

        rec = record("failed")
        rec.exc_info = sys.exc_info()
    redacted(rec)
    assert TEST_TOKEN not in rec.exc_text
    assert "boom with" in rec.exc_text


def test_json_formatter_emits_core_fields_and_extras():
    rec = record("hello %s", "world", trace={"intent": "time.now"})
    line = json.loads(JsonFormatter().format(rec))
    assert line["level"] == "INFO"
    assert line["logger"] == "coco_hub.test"
    assert line["msg"] == "hello world"
    assert line["trace"] == {"intent": "time.now"}
    assert line["ts"].endswith("Z")


def test_configure_logging_writes_redacted_json_lines_and_human_stderr(secret_settings):
    stream = io.StringIO()
    configure_logging(secret_settings, stream=stream)
    log = logging.getLogger("coco_hub.anything")
    log.info("hub token %s and key %s", TEST_TOKEN, YT_KEY, extra={"trace": {"intent": "x"}})
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/spotify/callback?code=C0de", "1.1", 200
    )
    for handler in logging.getLogger().handlers:
        handler.flush()

    path: Path = secret_settings.log_dir / "hub.jsonl"
    text = path.read_text()
    lines = [json.loads(line) for line in text.splitlines()]
    assert any(line["trace"] == {"intent": "x"} for line in lines)
    for secret in (TEST_TOKEN, YT_KEY, "C0de"):
        assert secret not in text
        assert secret not in stream.getvalue()
    assert "hub token" in stream.getvalue()
    assert not stream.getvalue().lstrip().startswith("{")


def test_configure_logging_is_idempotent(secret_settings):
    configure_logging(secret_settings, stream=io.StringIO())
    count = len(logging.getLogger().handlers)
    configure_logging(secret_settings, stream=io.StringIO())
    assert len(logging.getLogger().handlers) == count


def test_rotating_file_handler_limits(secret_settings):
    configure_logging(secret_settings, stream=io.StringIO())
    handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 5 * 1024 * 1024
    assert handlers[0].backupCount == 5


def test_noisy_http_client_loggers_are_quieted(secret_settings):
    configure_logging(secret_settings, stream=io.StringIO())
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
