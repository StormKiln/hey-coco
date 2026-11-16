"""Pairing a Display with a QR code or a one-time 6-digit code (#89)."""

import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from coco_hub import __main__ as hub_main
from coco_hub import db, identity, pairing
from coco_hub.app import create_app

from conftest import TEST_TOKEN

T0 = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
CERT_HEX = "986b1f9c5b9d9174cec25439c644aaf440ef0e107b3a36be5625c7f0fcb88922"


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.open_db(tmp_path)
    yield connection
    connection.close()


# --- The code store -----------------------------------------------------------------------


def test_issued_code_is_six_digits(conn):
    code = pairing.issue_code(conn, now=T0)
    assert re.fullmatch(r"[0-9]{6}", code)


def test_code_is_stored_hashed_not_in_plain_text(conn):
    code = pairing.issue_code(conn, now=T0)
    rows = conn.execute("SELECT * FROM pairing_code").fetchall()
    assert len(rows) == 1
    assert all(code not in str(value) for value in rows[0])


def test_right_code_redeems_once(conn):
    code = pairing.issue_code(conn, now=T0)
    assert pairing.redeem_code(conn, code, now=T0 + timedelta(seconds=30)) is pairing.Redeem.OK
    assert pairing.redeem_code(conn, code, now=T0 + timedelta(seconds=31)) is pairing.Redeem.NO_CODE


def test_no_code_issued(conn):
    assert pairing.redeem_code(conn, "123456", now=T0) is pairing.Redeem.NO_CODE


def test_code_expires_after_two_minutes(conn):
    code = pairing.issue_code(conn, now=T0)
    assert pairing.redeem_code(conn, code, now=T0 + timedelta(seconds=121)) is (
        pairing.Redeem.EXPIRED
    )


def test_code_is_still_good_just_before_expiry(conn):
    code = pairing.issue_code(conn, now=T0)
    assert pairing.redeem_code(conn, code, now=T0 + timedelta(seconds=119)) is pairing.Redeem.OK


def wrong_for(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_five_wrong_attempts_lock_the_code(conn):
    code = pairing.issue_code(conn, now=T0)
    results = [pairing.redeem_code(conn, wrong_for(code), now=T0) for _ in range(5)]
    assert results == [pairing.Redeem.WRONG] * 4 + [pairing.Redeem.LOCKED]
    # Even the right code is refused now.
    assert pairing.redeem_code(conn, code, now=T0) is pairing.Redeem.LOCKED


def test_wrong_attempts_below_the_limit_leave_the_code_usable(conn):
    code = pairing.issue_code(conn, now=T0)
    for _ in range(4):
        assert pairing.redeem_code(conn, wrong_for(code), now=T0) is pairing.Redeem.WRONG
    assert pairing.redeem_code(conn, code, now=T0) is pairing.Redeem.OK


def test_a_new_code_replaces_the_old_one(conn):
    first = pairing.issue_code(conn, now=T0)
    second = pairing.issue_code(conn, now=T0)
    if first != second:
        assert pairing.redeem_code(conn, first, now=T0) is pairing.Redeem.WRONG
    assert pairing.redeem_code(conn, second, now=T0) is pairing.Redeem.OK


def test_a_new_code_resets_a_locked_one(conn):
    code = pairing.issue_code(conn, now=T0)
    for _ in range(5):
        pairing.redeem_code(conn, wrong_for(code), now=T0)
    fresh = pairing.issue_code(conn, now=T0)
    assert pairing.redeem_code(conn, fresh, now=T0) is pairing.Redeem.OK


def test_another_process_sees_the_code(tmp_path: Path):
    """`make hub-pair` and the running Hub are separate processes sharing the database."""
    writer = db.open_db(tmp_path)
    reader = db.open_db(tmp_path)
    try:
        code = pairing.issue_code(writer, now=T0)
        assert pairing.redeem_code(reader, code, now=T0) is pairing.Redeem.OK
    finally:
        writer.close()
        reader.close()


# --- The pairing URI and QR code ----------------------------------------------------------


def test_pairing_uri_holds_host_port_token_and_full_cert_hash():
    uri = pairing.pairing_uri(host="192.168.1.20", port=8765, token="a b&c", cert_sha256=CERT_HEX)
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc) == ("cocohub", "pair")
    assert parse_qs(parts.query) == {
        "v": ["1"],
        "host": ["192.168.1.20"],
        "port": ["8765"],
        "token": ["a b&c"],
        "cert": [CERT_HEX],
    }
    assert "+" not in uri  # Swift's URLComponents reads "+" literally


def test_pairing_uri_matches_the_display_test_vector():
    """CocoKit's PairingURITests parses this exact string."""
    uri = pairing.pairing_uri(host="192.168.1.20", port=8765, token="a b&c=d", cert_sha256=CERT_HEX)
    assert uri == (
        "cocohub://pair?v=1&host=192.168.1.20&port=8765&token=a%20b%26c%3Dd&cert=" + CERT_HEX
    )


def test_qr_is_rendered_with_half_blocks():
    art = pairing.render_qr("cocohub://pair?v=1")
    assert "▀" in art or "▄" in art
    assert len(art.splitlines()) > 10


def test_qr_draws_dark_modules_black_on_white_with_a_quiet_zone():
    import segno

    data = "cocohub://pair?v=1&host=10.0.0.7"
    expected = [
        [bool(m) for m in row]
        for row in segno.make(data, error="m", micro=False).matrix_iter(scale=1, border=4)
    ]
    lines = pairing.render_qr(data).splitlines()
    assert all(line.startswith("\x1b[30;47m") and line.endswith("\x1b[0m") for line in lines)
    top = {" ": False, "▀": True, "▄": False, "█": True}
    bottom = {" ": False, "▀": False, "▄": True, "█": True}
    drawn: list[list[bool]] = []
    for line in lines:
        cells = line.removeprefix("\x1b[30;47m").removesuffix("\x1b[0m")
        drawn.append([top[c] for c in cells])
        drawn.append([bottom[c] for c in cells])
    assert drawn[: len(expected)] == expected
    assert not any(drawn[0]) and not any(drawn[-1])


def test_host_hint_prefers_the_configured_host(settings):
    bound = settings.model_copy(update={"host": "10.0.0.7"})
    assert pairing.host_hint(bound) == "10.0.0.7"


# --- POST /v1/pair ------------------------------------------------------------------------


@pytest.fixture
def clock():
    now = {"t": T0}
    return now


@pytest.fixture
def client(settings, clock):
    app = create_app(settings, clock=lambda: clock["t"])
    with TestClient(app) as c:
        yield c


def issue(settings) -> str:
    conn = db.open_db(settings.data_dir)
    try:
        return pairing.issue_code(conn, now=T0)
    finally:
        conn.close()


def test_pair_with_the_right_code_returns_the_token_without_a_bearer(client, settings):
    code = issue(settings)
    response = client.post("/v1/pair", json={"code": code})
    assert response.status_code == 200
    assert response.json() == {"token": TEST_TOKEN}


def test_pair_code_is_single_use(client, settings):
    code = issue(settings)
    assert client.post("/v1/pair", json={"code": code}).status_code == 200
    again = client.post("/v1/pair", json={"code": code})
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "no_pairing_code"


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("none", 404, "no_pairing_code"),
        ("wrong", 401, "pairing_code_wrong"),
        ("expired", 410, "pairing_code_expired"),
        ("locked", 429, "pairing_code_locked"),
    ],
)
def test_pair_errors(client, settings, clock, setup, status, code):
    sent = "000000"
    if setup != "none":
        real = issue(settings)
        sent = wrong_for(real) if setup != "expired" else real
        if setup == "expired":
            clock["t"] = T0 + timedelta(minutes=3)
        if setup == "locked":
            for _ in range(4):
                client.post("/v1/pair", json={"code": sent})
    response = client.post("/v1/pair", json={"code": sent})
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert TEST_TOKEN not in response.text


def test_wrong_code_five_times_then_right_code_is_locked(client, settings):
    real = issue(settings)
    for _ in range(5):
        client.post("/v1/pair", json={"code": wrong_for(real)})
    response = client.post("/v1/pair", json={"code": real})
    assert response.status_code == 429


@pytest.mark.parametrize(
    "body", [{}, {"code": "12345"}, {"code": "1234567"}, {"code": "abcdef"}, {"code": 123456}]
)
def test_pair_rejects_malformed_codes_without_using_an_attempt(client, settings, body):
    real = issue(settings)
    for _ in range(5):
        assert client.post("/v1/pair", json=body).status_code == 422
    assert client.post("/v1/pair", json={"code": real}).status_code == 200


def test_pair_never_logs_the_code_or_token(client, settings, caplog):
    caplog.set_level(logging.DEBUG)
    real = issue(settings)
    client.post("/v1/pair", json={"code": wrong_for(real)})
    client.post("/v1/pair", json={"code": real})
    assert real not in caplog.text
    assert wrong_for(real) not in caplog.text
    assert TEST_TOKEN not in caplog.text


# Routes under /v1 that answer without the bearer token. Everything else must require it.
OPEN_V1_ROUTES = {("POST", "/v1/pair")}


def test_every_other_v1_route_requires_the_bearer(client):
    paths = client.app.openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/v1")
        for method in operations
    }
    assert OPEN_V1_ROUTES <= routes
    assert len(routes) > 5
    for method, path in sorted(routes):
        response = client.request(method, re.sub(r"\{[^}]+\}", "x", path))
        if (method, path) in OPEN_V1_ROUTES:
            assert response.status_code != 401, (method, path)
        else:
            assert response.status_code == 401, f"{method} {path} answers without the bearer"
            assert response.json()["error"]["code"] == "unauthorized"


# --- python -m coco_hub --pair ------------------------------------------------------------


def test_pair_command_prints_qr_code_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("COCO_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("COCO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COCO_HOST", "10.0.0.7")
    monkeypatch.setenv("COCO_PORT", "59510")
    assert hub_main.main(["--pair"]) == 0
    out = capsys.readouterr().out
    ident = identity.load_or_create(tmp_path / "data")
    uri = pairing.pairing_uri(
        host="10.0.0.7", port=59510, token=TEST_TOKEN, cert_sha256=ident.fingerprint
    )
    assert pairing.render_qr(uri) in out
    assert ident.short_fingerprint in out
    # The token travels only inside the QR code, never as text.
    assert TEST_TOKEN not in out

    match = re.search(r"\b(\d{3}) (\d{3})\b", out)
    assert match, out
    conn = db.open_db(tmp_path / "data")
    try:
        code = match.group(1) + match.group(2)
        assert pairing.redeem_code(conn, code, now=datetime.now(UTC)) is pairing.Redeem.OK
    finally:
        conn.close()


def test_pair_code_table_has_no_metadata_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pairing_code)")]
    assert cols and "metadata" not in cols


def test_redeem_is_safe_on_a_fresh_connection(tmp_path: Path):
    conn = sqlite3.connect(tmp_path / "plain.db")
    try:
        db.migrate(conn)
        assert pairing.redeem_code(conn, "123456", now=T0) is pairing.Redeem.NO_CODE
    finally:
        conn.close()
