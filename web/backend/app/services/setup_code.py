"""The one-time code that lets the first administrator be created.

A fresh installation answers on the network before anyone has an account. The
code is what keeps "whoever gets there first" from becoming the administrator:
it is printed on the host's console by `prtg-nats setup` or
`prtg-nats setup-code`, so finishing the setup needs a shell on that host.

Only a hash lives in the runtime, next to the other private material. The code
itself is shown once and never stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import Settings

# Long enough to go and open the browser, short enough that a code someone
# photographed off a screen last week is worthless.
LIFETIME = timedelta(hours=24)

# Crockford's base32 without the letters people misread: no I, L, O, U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Sixteen characters of five bits each: 80 bits, out of reach for guessing
# over HTTP within the lifetime.
_LENGTH = 16
_GROUP = 4


def _normalise(code: str) -> str:
    return "".join(code.split()).replace("-", "").upper()


def _digest(code: str) -> str:
    return hashlib.sha256(_normalise(code).encode("ascii", "replace")).hexdigest()


class SetupCodeStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def path(self) -> Path:
        return self._settings.private_dir / "setup-code"

    def issue(self) -> str:
        """A new code; the previous one stops working."""
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        code = "-".join(raw[i : i + _GROUP] for i in range(0, _LENGTH, _GROUP))
        self.store(code)
        return code

    def store(self, code: str) -> None:
        """Remember the hash of `code`. issue() is the way in; tests use this."""
        record = {
            "sha256": _digest(code),
            "expires_at": (datetime.now(UTC) + LIFETIME).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        # Written beside the target and renamed, so a reader never sees half a
        # record, and created 0600 so it is never readable by anyone else.
        partial = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        partial.replace(self.path)

    @property
    def expires_at(self) -> datetime | None:
        record = self._read()
        return None if record is None else record[1]

    def matches(self, code: str) -> bool:
        record = self._read()
        if record is None:
            return False
        digest, expires_at = record
        if expires_at <= datetime.now(UTC):
            return False
        return hmac.compare_digest(digest, _digest(code))

    @property
    def _claimed(self) -> Path:
        return self.path.with_name(f"{self.path.name}.claimed")

    def claim(self, code: str) -> bool:
        """Take the code for one setup attempt, or say it is not available.

        A rename, so of two requests carrying the same code only one gets it:
        the other finds no file and is refused, instead of both racing to
        create an administrator.
        """
        if not self.matches(code):
            return False
        try:
            self.path.rename(self._claimed)
        except FileNotFoundError:
            return False
        return True

    def release(self) -> None:
        """Give a claimed code back after a setup attempt that failed."""
        if self._claimed.exists() and not self.path.exists():
            self._claimed.rename(self.path)

    def consume(self) -> None:
        """Forget the code for good, once the administrator is committed."""
        self._claimed.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)

    def _read(self) -> tuple[str, datetime] | None:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            return str(record["sha256"]), datetime.fromisoformat(record["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            # Missing or unreadable is the same answer: no valid code.
            return None
