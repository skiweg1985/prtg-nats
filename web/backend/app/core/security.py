"""Password hashing and session tokens."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Defaults from argon2-cffi's RFC 9106 low-memory profile: 64 MiB and three
# passes. On the class of machine that runs a NATS server this is a few tens of
# milliseconds, which is the right trade for a login form.
_hasher = PasswordHasher()

SESSION_TOKEN_BYTES = 32
MINIMUM_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """SHA-256 is right here where Argon2 is not.

    The token is 256 bits of randomness we generated, so there is nothing to
    brute-force and nothing to slow down; what matters is that a database dump
    cannot be replayed as a live session.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def password_problems(password: str) -> list[str]:
    """Field-level complaints, as translation key suffixes.

    Length only. Composition rules push people towards "Passw0rd!" and every
    modern guideline has dropped them; this platform sits behind a reverse
    proxy on an internal network and throttles attempts server-side.
    """
    problems: list[str] = []
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        problems.append("too_short")
    if password.strip() != password:
        problems.append("surrounding_whitespace")
    return problems
