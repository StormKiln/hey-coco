"""Tests for asc.py. Recorded App Store Connect responses only: nothing here calls Apple.

make repo-check   (runs these with the Hub's locked environment, for `cryptography`)
"""

import base64
import json
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import asc  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "asc"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class JwtTest(unittest.TestCase):
    def test_header_and_claims(self):
        body = asc.signing_input("KEY123", "issuer-uuid", now=1_000_000).decode()
        header, claims = (json.loads(b64decode(p)) for p in body.split("."))
        self.assertEqual(header, {"alg": "ES256", "kid": "KEY123", "typ": "JWT"})
        self.assertEqual(
            claims,
            {"iss": "issuer-uuid", "iat": 1_000_000, "exp": 1_001_200, "aud": "appstoreconnect-v1"},
        )

    def test_lifetime_within_apples_twenty_minutes(self):
        claims = asc.jwt_claims("i", 0)
        self.assertLessEqual(claims["exp"] - claims["iat"], 20 * 60)

    def test_raw_signature_is_64_bytes_padded(self):
        raw = asc.der_to_raw(1, 2)
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[31], 1)
        self.assertEqual(raw[63], 2)

    def test_token_verifies_with_the_public_key(self):
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        except ImportError:
            self.skipTest("cryptography not installed (make repo-check provides it)")
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        token = asc.make_token("KEY123", "issuer-uuid", pem, now=1_000_000)
        header, claims, sig = token.split(".")
        raw = b64decode(sig)
        self.assertEqual(len(raw), 64)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_rejects_a_non_p256_key(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            self.skipTest("cryptography not installed")
        pem = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with self.assertRaises(asc.AscError):
            asc.make_token("K", "I", pem)


class BuildNumberTest(unittest.TestCase):
    def test_highest_across_pages_is_numeric_not_lexicographic(self):
        pages = [fixture("builds_page1.json"), fixture("builds_page2.json")]
        self.assertEqual(asc.builds_in(pages), ["41", "9", "100"])
        self.assertEqual(asc.highest_build(asc.builds_in(pages)), "100")

    def test_no_builds_yet(self):
        self.assertIsNone(asc.highest_build(asc.builds_in([fixture("builds_empty.json")])))
        asc.check_build_number("1", None, "0.1.0")

    def test_dotted_build_numbers_compare_by_part(self):
        self.assertEqual(asc.highest_build(["1.9", "1.10", "junk"]), "1.10")

    def test_greater_passes(self):
        asc.check_build_number("101", "100", "0.1.0")

    def test_equal_or_lower_refuses_with_a_hint(self):
        for build in ("100", "99"):
            with self.assertRaises(asc.AscError) as ctx:
                asc.check_build_number(build, "100", "0.1.0")
            self.assertIn("BUILD_NUMBER=", str(ctx.exception))
            self.assertIn("0.1.0", str(ctx.exception))


class ProcessingTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(
            asc.processing_state(fixture("status_processing.json"), "0.1.0", "120"), "PROCESSING"
        )
        self.assertEqual(
            asc.processing_state(fixture("status_valid.json"), "0.1.0", "120"), "VALID"
        )
        self.assertEqual(
            asc.processing_state(fixture("builds_empty.json"), "0.1.0", "120"), "NOT_VISIBLE_YET"
        )

    def test_identity_mismatch_is_an_error(self):
        with self.assertRaises(asc.AscError):
            asc.processing_state(fixture("status_valid.json"), "0.2.0", "120")
        with self.assertRaises(asc.AscError):
            asc.processing_state(fixture("status_valid.json"), "0.1.0", "121")

    def test_more_than_one_match_is_an_error(self):
        payload = fixture("status_valid.json")
        payload["data"] = payload["data"] * 2
        with self.assertRaises(asc.AscError):
            asc.processing_state(payload, "0.1.0", "120")

    def poll(self, responses, attempts=10):
        it = iter(responses)
        sleeps, logs = [], []
        result = asc.poll(
            lambda: fixture(next(it)),
            "0.1.0",
            "120",
            attempts=attempts,
            sleep=sleeps.append,
            log=logs.append,
        )
        return result, sleeps, logs

    def test_poll_until_valid_logs_each_change_once(self):
        result, sleeps, logs = self.poll(
            [
                "builds_empty.json",
                "status_processing.json",
                "status_processing.json",
                "status_valid.json",
            ]
        )
        self.assertEqual(result, "VALID")
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(
            logs,
            [
                "Build processing: NOT_VISIBLE_YET",
                "Build processing: PROCESSING",
                "Build processing: VALID",
            ],
        )

    def test_poll_invalid_fails(self):
        with self.assertRaises(asc.AscError) as ctx:
            self.poll(["status_processing.json", "status_invalid.json"])
        self.assertIn("INVALID", str(ctx.exception))

    def test_poll_times_out_as_pending_not_success(self):
        result, sleeps, _ = self.poll(["status_processing.json"] * 3, attempts=3)
        self.assertIsNone(result)
        self.assertEqual(len(sleeps), 2)


class InputsTest(unittest.TestCase):
    def test_validate(self):
        asc.validate("com.stormkiln.heycoco.display", "0.1.0", "120")
        for bad in (("nobundle", "0.1.0", "1"), ("a.b", "v0.1", "1"), ("a.b", "0.1.0", "1a")):
            with self.assertRaises(asc.AscError):
                asc.validate(*bad)

    def test_missing_credentials_named(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"ASC_KEY_ID": "K"}, clear=True):
            with self.assertRaises(asc.AscError) as ctx:
                asc.credentials()
        self.assertIn("ASC_ISSUER_ID", str(ctx.exception))
        self.assertIn("ASC_KEY_PATH", str(ctx.exception))
        self.assertNotIn("ASC_KEY_ID,", str(ctx.exception))

    def test_not_on_main_refuses(self):
        def run(returncode):
            return lambda *a, **k: subprocess.CompletedProcess(a, returncode)

        asc.check_on_main(run(0))
        with self.assertRaises(asc.AscError) as ctx:
            asc.check_on_main(run(1))
        self.assertIn("origin/main", str(ctx.exception))
        with self.assertRaises(asc.AscError):
            asc.check_on_main(run(128))


if __name__ == "__main__":
    unittest.main()
