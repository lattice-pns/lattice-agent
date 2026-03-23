"""Tests for tools/lattice_auth.py — Ed25519 header construction for Lattice."""

from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.lattice_auth import get_auth_headers, get_post_auth_headers

# Deterministic 32-byte private key (valid Ed25519); never use in production.
TEST_PRIVKEY_HEX = "00" * 32


def _privkey() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(TEST_PRIVKEY_HEX))


class TestGetAuthHeaders:
    """GET requests sign `;{timestamp}`."""

    def test_builds_headers_and_verifies_signature(self):
        with patch("tools.lattice_auth.time.time", return_value=1_700_000_000):
            headers = get_auth_headers(TEST_PRIVKEY_HEX)

        assert headers["X-Timestamp"] == "1700000000"
        assert len(headers["X-Agent-Pubkey"]) == 64
        assert len(headers["X-Signature"]) == 128

        msg = b";1700000000"
        sig = bytes.fromhex(headers["X-Signature"])
        pubkey = _privkey().public_key()
        pubkey.verify(sig, msg)

    def test_invalid_key_length_raises(self):
        with pytest.raises(ValueError, match="64 hex"):
            get_auth_headers("abcd")


class TestGetPostAuthHeaders:
    """POST requests sign `{body_str};{timestamp}` (exact JSON string)."""

    def test_builds_headers_and_verifies_signature(self):
        body_str = '{"to":"aa","body":"hi"}'
        with patch("tools.lattice_auth.time.time", return_value=1_700_000_001):
            headers = get_post_auth_headers(TEST_PRIVKEY_HEX, body_str)

        assert headers["X-Timestamp"] == "1700000001"
        expected_msg = f"{body_str};1700000001".encode("utf-8")
        sig = bytes.fromhex(headers["X-Signature"])
        _privkey().public_key().verify(sig, expected_msg)

    def test_invalid_key_length_raises(self):
        with pytest.raises(ValueError, match="64 hex"):
            get_post_auth_headers("ff" * 10, "{}")
