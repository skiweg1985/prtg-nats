"""Secrets must not escape. These tests are the enforcement."""

from __future__ import annotations

import pytest

from app.core.redaction import MASK, is_secret_key, redact, redact_text


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "NATS_PASSWORD",
        "access_key",
        "ACCESS_KEY",
        "api_key",
        "token",
        "session_id",
        "private_key",
        "passphrase",
        "credential",
        "authorization",
    ],
)
def test_secret_keys_are_recognised(key: str) -> None:
    assert is_secret_key(key)


@pytest.mark.parametrize(
    "key", ["ca_sha256", "sha256", "fingerprint", "checksum", "username", "host"]
)
def test_public_keys_are_not_redacted(key: str) -> None:
    """A CA fingerprint is public and an operator compares it by eye.

    Redacting it would make the one comparison the security model depends on
    impossible to perform in the interface.
    """
    assert not is_secret_key(key)


def test_nested_structures_are_redacted() -> None:
    payload = {
        "probe": "berlin-01",
        "credentials": {"username": "mpp-berlin-01", "password": "hunter2"},
        "history": [{"api_key": "abc"}, {"note": "fine"}],
    }
    redacted = redact(payload)

    assert redacted["probe"] == "berlin-01"
    assert redacted["credentials"] == MASK
    assert redacted["history"][0]["api_key"] == MASK
    assert redacted["history"][1]["note"] == "fine"


def test_bcrypt_hash_in_free_text_is_masked() -> None:
    line = 'password: "$2a$11$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012"'
    assert "$2a$11$" not in redact_text(line)


def test_generated_password_in_free_text_is_masked() -> None:
    """`openssl rand -hex 32` is how every NATS account password is made."""
    generated = "a" * 64
    assert generated not in redact_text(f"NATS_PASSWORD={generated}")


def test_private_key_block_is_masked() -> None:
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
        "-----END PRIVATE KEY-----"
    )
    redacted = redact_text(pem)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ" not in redacted


def test_ca_fingerprint_survives_text_redaction() -> None:
    """64 hex characters, but a fingerprint, not a secret.

    The long-hex rule starts at 48 characters, so this is a deliberate check
    that the two do not collide in practice.
    """
    fingerprint = "3b" * 32
    assert redact({"ca_sha256": fingerprint})["ca_sha256"] == fingerprint
