"""Pairing a Display: a QR code and a one-time 6-digit code (#89).

``make hub-pair`` (``python -m coco_hub --pair``) issues a code and prints, in the terminal
only, a QR code holding the pairing URI (host hint, port, token, full certificate SHA-256)
plus the code and the short fingerprint. The code lives in the Hub database (hashed, salted),
so the separately running Hub server can check it on ``POST /v1/pair``
(docs/protocol/README.md, "Pairing").

A code is valid for two minutes, works once, and is locked after five wrong attempts. Issuing a
new code replaces the previous one. Neither the code nor the token is ever logged.
"""

import hashlib
import hmac
import io
import secrets
import socket
import sqlite3
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import quote, urlencode

import segno

from coco_hub.discovery import lan_addresses
from coco_hub.settings import Settings

CODE_TTL = timedelta(minutes=2)
MAX_FAILED_ATTEMPTS = 5
URI_SCHEME = "cocohub"
URI_VERSION = 1


class Redeem(StrEnum):
    OK = "ok"
    NO_CODE = "no_code"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"


def _hash(code: str, salt: str) -> str:
    return hmac.new(bytes.fromhex(salt), code.encode(), hashlib.sha256).hexdigest()


def issue_code(conn: sqlite3.Connection, *, now: datetime) -> str:
    """A fresh 6-digit code, replacing any earlier one. Only its salted hash is stored."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pairing_code "
            "(id, code_hash, salt, expires_at, failed_attempts, locked) "
            "VALUES (1, ?, ?, ?, 0, 0)",
            (_hash(code, salt), salt, (now + CODE_TTL).timestamp()),
        )
    return code


def redeem_code(conn: sqlite3.Connection, code: str, *, now: datetime) -> Redeem:
    """Check ``code`` against the active one. Success consumes it; a wrong code counts an
    attempt, and the fifth wrong attempt locks it. Expired and locked codes stay until
    replaced, so the Display can say why."""
    # IMMEDIATE: take the write lock before reading, so two guesses (or the Hub and
    # `make hub-pair`) can't both read the same attempt count.
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT code_hash, salt, expires_at, failed_attempts, locked "
            "FROM pairing_code WHERE id = 1"
        ).fetchone()
        if row is None:
            result = Redeem.NO_CODE
        else:
            code_hash, salt, expires_at, failed, locked = row
            if locked:
                result = Redeem.LOCKED
            elif now.timestamp() >= expires_at:
                result = Redeem.EXPIRED
            elif hmac.compare_digest(_hash(code, salt), code_hash):
                conn.execute("DELETE FROM pairing_code WHERE id = 1")
                result = Redeem.OK
            else:
                failed += 1
                lock = failed >= MAX_FAILED_ATTEMPTS
                conn.execute(
                    "UPDATE pairing_code SET failed_attempts = ?, locked = ? WHERE id = 1",
                    (failed, int(lock)),
                )
                result = Redeem.LOCKED if lock else Redeem.WRONG
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return result


def pairing_uri(*, host: str, port: int, token: str, cert_sha256: str) -> str:
    """``cocohub://pair?v=1&host=…&port=…&token=…&cert=<64 hex>`` (query values URL-encoded)."""
    query = urlencode(
        {"v": URI_VERSION, "host": host, "port": port, "token": token, "cert": cert_sha256},
        # %20, not "+", for spaces: Swift's URLComponents doesn't read "+" as a space.
        quote_via=quote,
        safe="",
    )
    return f"{URI_SCHEME}://pair?{query}"


_HALF_BLOCKS = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
# Black on white, so the code reads the same in light and dark terminal themes (a QR code
# drawn light-on-dark is inverted, which not every scanner reads).
_BLACK_ON_WHITE, _RESET = "\x1b[30;47m", "\x1b[0m"


def render_qr(data: str) -> str:
    """``data`` as a QR code drawn with Unicode half blocks (two modules per character row)."""
    rows = [
        [bool(module) for module in row]
        for row in segno.make(data, error="m", micro=False).matrix_iter(scale=1, border=4)
    ]
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    out = io.StringIO()
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        line = "".join(_HALF_BLOCKS[pair] for pair in zip(top, bottom, strict=True))
        out.write(f"{_BLACK_ON_WHITE}{line}{_RESET}\n")
    return out.getvalue()


def host_hint(settings: Settings) -> str:
    """Where the Display should look for the Hub: the bound host, else the first LAN IPv4,
    else the Mac's host name. Only a hint: the Display trusts the certificate, not the address."""
    addresses = lan_addresses(settings)
    if addresses:
        return addresses[0]
    return socket.gethostname()
