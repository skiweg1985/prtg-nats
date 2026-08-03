"""Keep secrets out of logs, audit records and API responses.

This module is deliberately paranoid. It is cheaper to redact a field that was
harmless than to discover a password in an audit trail that is, by design,
immutable.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "••••••••"

# Substring match against the lower-cased key. A field called
# "password_changed_at" is redacted too - a date is a small price for the
# guarantee that nothing named like a secret ever escapes.
SECRET_KEY_HINTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private_key",
    "privatekey",
    "access_key",
    "accesskey",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session_id",
    "bcrypt",
    "hash",
    "salt",
    "passphrase",
)

# Values that look like a secret regardless of the key they sit under: PEM
# blocks, bcrypt hashes, and the hex strings "openssl rand -hex 32" produces
# for every NATS account.
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)
_BCRYPT = re.compile(r"\$2[abxy]\$[0-9]{2}\$[./A-Za-z0-9]{53}")
_LONG_HEX = re.compile(r"\b[0-9a-f]{48,}\b")

# A SHA-256 digest and an `openssl rand -hex 32` password are both 64 hex
# characters, so free text cannot tell them apart and the conservative rule
# above wins there. In structured data the key name settles it: these carry
# public fingerprints an operator has to compare by eye, and masking them would
# break the one check the trust model depends on.
FINGERPRINT_KEYS: frozenset[str] = frozenset(
    {"ca_sha256", "sha256", "fingerprint", "checksum", "expected", "actual"}
)


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in FINGERPRINT_KEYS:
        return False
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def is_public_key(key: str) -> bool:
    """Keys whose value is published on purpose and passes through verbatim."""
    return key.lower() in FINGERPRINT_KEYS


def redact_text(value: str) -> str:
    """Mask secret-shaped substrings inside free text such as command output."""
    value = _PEM_BLOCK.sub(
        f"-----BEGIN PRIVATE KEY-----{MASK}-----END PRIVATE KEY-----", value
    )
    value = _BCRYPT.sub(MASK, value)
    return _LONG_HEX.sub(MASK, value)


def redact(value: Any, *, _key: str | None = None) -> Any:
    """Return a copy of ``value`` with every secret replaced by ``MASK``.

    Works on the shapes that actually reach us: dicts, lists, tuples, strings
    and scalars. Unknown objects are returned untouched - they never reach the
    wire without passing through a Pydantic model first.
    """
    if _key is not None:
        if is_secret_key(_key):
            return MASK if value is not None else None
        # A fingerprint is public and must survive verbatim - the long-hex rule
        # in redact_text() cannot distinguish it from a generated password, so
        # the key name has to be trusted here or the value never gets through.
        if is_public_key(_key) and isinstance(value, str):
            return value

    if isinstance(value, dict):
        return {str(k): redact(v, _key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
