"""Hub TLS identity: self-signed cert + key + Hub ID in data_dir (#54)."""

import hashlib
import ipaddress
import re
import stat
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from coco_hub import identity

SHORT_FP = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){5}$")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_first_run_creates_cert_key_and_id(tmp_path: Path):
    ident = identity.load_or_create(tmp_path)
    assert ident.cert_path.is_file()
    assert ident.key_path.is_file()
    assert re.fullmatch(r"[0-9a-f]{16}", ident.hub_id)
    assert mode(ident.key_path) == 0o600
    assert mode(ident.key_path.parent) == 0o700


def test_identity_is_stable_across_restarts(tmp_path: Path):
    first = identity.load_or_create(tmp_path)
    cert_bytes = first.cert_path.read_bytes()
    second = identity.load_or_create(tmp_path)
    assert second.hub_id == first.hub_id
    assert second.fingerprint == first.fingerprint
    assert second.cert_path.read_bytes() == cert_bytes


def test_fingerprint_is_sha256_of_der_cert(tmp_path: Path):
    ident = identity.load_or_create(tmp_path)
    cert = x509.load_pem_x509_certificate(ident.cert_path.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    assert ident.fingerprint == hashlib.sha256(der).hexdigest()
    assert ident.der == der


def test_short_fingerprint_is_six_groups_of_four_upper_hex(tmp_path: Path):
    ident = identity.load_or_create(tmp_path)
    assert SHORT_FP.match(ident.short_fingerprint)
    assert ident.short_fingerprint.replace("-", "") == ident.fingerprint[:24].upper()


def test_format_fingerprint():
    digest = "0123456789abcdef" * 4
    assert identity.short_fingerprint(digest) == "0123-4567-89AB-CDEF-0123-4567"


def test_cert_is_self_signed_p256_with_loopback_names(tmp_path: Path):
    ident = identity.load_or_create(tmp_path)
    cert = x509.load_pem_x509_certificate(ident.cert_path.read_bytes())
    key = cert.public_key()
    assert isinstance(key, ec.EllipticCurvePublicKey)
    assert key.curve.name == "secp256r1"
    assert cert.issuer == cert.subject
    assert ident.hub_id in cert.subject.rfc4514_string()
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)
    assert "localhost" in san.get_values_for_type(x509.DNSName)


def test_loose_key_permissions_are_tightened(tmp_path: Path):
    ident = identity.load_or_create(tmp_path)
    ident.key_path.chmod(0o644)
    identity.load_or_create(tmp_path)
    assert mode(ident.key_path) == 0o600


def test_missing_cert_regenerates_cert_but_keeps_hub_id(tmp_path: Path):
    first = identity.load_or_create(tmp_path)
    first.cert_path.unlink()
    second = identity.load_or_create(tmp_path)
    assert second.hub_id == first.hub_id
    assert second.fingerprint != first.fingerprint


def test_cert_not_matching_key_is_replaced(tmp_path: Path):
    first = identity.load_or_create(tmp_path / "a")
    other = identity.load_or_create(tmp_path / "b")
    first.cert_path.write_bytes(other.cert_path.read_bytes())
    fixed = identity.load_or_create(tmp_path / "a")
    assert fixed.fingerprint not in (first.fingerprint, other.fingerprint)


@pytest.mark.parametrize("junk", [b"", b"not a cert"])
def test_corrupt_cert_is_replaced(tmp_path: Path, junk: bytes):
    first = identity.load_or_create(tmp_path)
    first.cert_path.write_bytes(junk)
    assert SHORT_FP.match(identity.load_or_create(tmp_path).short_fingerprint)
