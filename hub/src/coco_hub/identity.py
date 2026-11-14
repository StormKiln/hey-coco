"""The Hub's TLS identity: a self-signed EC P-256 certificate, its key and a stable Hub ID.

Created on first run in ``<data_dir>/tls/`` and reused afterwards, so the fingerprint the
Display pinned when pairing stays valid across restarts (see docs/protocol/README.md,
"Transport and pairing"). The fingerprint is the SHA-256 of the DER certificate; people
compare its short form (the first 24 hex digits as six groups of four, e.g.
``3F2A-91C0-…``) on the Hub (``make hub-fingerprint``) and on the iPad.

Nothing here is trusted by a certificate authority: the Display trusts the Hub because the
person who paired it checked the fingerprint (trust on first use).
"""

import datetime as dt
import hashlib
import ipaddress
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

log = logging.getLogger(__name__)

TLS_DIRNAME = "tls"
CERT_FILENAME = "hub.crt"
KEY_FILENAME = "hub.key"
HUB_ID_FILENAME = "hub_id"
# Long-lived on purpose: the Display pins this exact certificate, so a new one means
# re-pairing. Nothing checks the validity dates (the pin replaces CA trust).
VALIDITY = dt.timedelta(days=365 * 20)


@dataclass(frozen=True)
class HubIdentity:
    hub_id: str
    cert_path: Path
    key_path: Path
    der: bytes
    """The certificate in DER form (what the fingerprint hashes)."""

    @property
    def fingerprint(self) -> str:
        """Lowercase hex SHA-256 of the DER certificate (64 characters)."""
        return hashlib.sha256(self.der).hexdigest()

    @property
    def short_fingerprint(self) -> str:
        return short_fingerprint(self.fingerprint)


def short_fingerprint(hex_digest: str) -> str:
    """``ABCD-EF01-2345-6789-ABCD-EF01``: the first 24 hex digits, upper case, in fours."""
    head = hex_digest[:24].upper()
    return "-".join(head[i : i + 4] for i in range(0, 24, 4))


def load_or_create(data_dir: Path) -> HubIdentity:
    """Load the identity from ``data_dir/tls``, creating whatever is missing or unusable."""
    tls_dir = data_dir / TLS_DIRNAME
    tls_dir.mkdir(parents=True, exist_ok=True)
    tls_dir.chmod(0o700)
    hub_id = _load_or_create_hub_id(tls_dir / HUB_ID_FILENAME)
    cert_path, key_path = tls_dir / CERT_FILENAME, tls_dir / KEY_FILENAME

    der = _load_existing(cert_path, key_path)
    if der is None:
        replacing = cert_path.exists() or key_path.exists()
        der = _create(cert_path, key_path, hub_id)
        fingerprint = short_fingerprint(hashlib.sha256(der).hexdigest())
        if replacing:
            log.warning(
                "Replaced an unusable Coco Hub TLS certificate (new fingerprint %s); "
                "paired Displays must re-pair",
                fingerprint,
            )
        else:
            log.info("Created the Coco Hub TLS certificate (fingerprint %s)", fingerprint)
    key_path.chmod(0o600)
    return HubIdentity(hub_id=hub_id, cert_path=cert_path, key_path=key_path, der=der)


def _load_or_create_hub_id(path: Path) -> str:
    try:
        existing = path.read_text().strip()
    except FileNotFoundError:
        existing = ""
    if len(existing) == 16 and all(c in "0123456789abcdef" for c in existing):
        return existing
    hub_id = secrets.token_hex(8)
    _write_private(path, hub_id.encode() + b"\n", 0o644)
    return hub_id


def _load_existing(cert_path: Path, key_path: Path) -> bytes | None:
    """The DER cert if both files load and belong together, else ``None``."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        return None
    if cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        return None
    return cert.public_bytes(serialization.Encoding.DER)


def _create(cert_path: Path, key_path: Path, hub_id: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Coco Hub {hub_id}")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + VALIDITY)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_private(key_path, key_pem, 0o600)
    _write_private(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return cert.public_bytes(serialization.Encoding.DER)


def _write_private(path: Path, data: bytes, mode: int) -> None:
    """Write atomically; the file is created with ``mode`` (never briefly world-readable)."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
