"""The pinned SSH host keys, and the rule for changing them.

One file, shared with anything else that talks to a probe. It is the reason a
management connection cannot be answered by something that merely reached the
address first, so the rule for writing it is narrow:

    A host that is not pinned yet gets pinned to what it reported.
    A host that is pinned and presents something else is an error.

The second half is the whole point. Overwriting on mismatch would turn the
file into a record of whatever answered last, which is not pinning at all.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import HostKeyMismatchError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HostKey:
    """One key as OpenSSH writes it: an algorithm and its base64 blob."""

    algorithm: str
    blob: str

    @classmethod
    def parse(cls, line: str) -> HostKey | None:
        parts = line.strip().split()
        if len(parts) < 2:
            return None
        # A reported line may or may not carry the trailing comment.
        algorithm, blob = parts[0], parts[1]
        if not algorithm or not blob:
            return None
        try:
            base64.b64decode(blob, validate=True)
        except Exception:
            return None
        return cls(algorithm=algorithm, blob=blob)

    @property
    def fingerprint(self) -> str:
        """SHA256:… , the same string ssh-keygen -l prints."""
        digest = hashlib.sha256(base64.b64decode(self.blob)).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    def __str__(self) -> str:
        return f"{self.algorithm} {self.blob}"


def host_pattern(host: str, port: int) -> str:
    """How OpenSSH spells a host in this file. Port 22 stays bare."""
    return host if port == 22 else f"[{host}]:{port}"


def _matches(field: str, pattern: str) -> bool:
    """Whether a known_hosts host field refers to `pattern`.

    Handles both spellings: the plain comma-separated list, and the hashed
    form ssh-keyscan -H writes, where the field is HMAC-SHA1 of the host name
    under a per-entry salt. The shell tooling wrote hashed entries, so an
    installation that predates this module has them and they still have to be
    recognised - otherwise every existing probe would look unpinned.
    """
    if field.startswith("|1|"):
        _, _, salt_b64, hash_b64 = field.split("|", 3)
        try:
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
        except Exception:
            return False
        actual = hmac.new(salt, pattern.encode(), hashlib.sha1).digest()
        return hmac.compare_digest(actual, expected)
    return pattern in field.split(",")


def read_pinned(path: Path, host: str, port: int) -> tuple[HostKey, ...]:
    """Every key currently pinned for this host."""
    if not path.is_file():
        return ()
    pattern = host_pattern(host, port)
    found: list[HostKey] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        field, _, rest = line.partition(" ")
        if not _matches(field, pattern):
            continue
        key = HostKey.parse(rest)
        if key is not None:
            found.append(key)
    return tuple(found)


def hash_host(pattern: str) -> str:
    """The |1|salt|hash spelling, so the file does not list its own hosts."""
    salt = secrets.token_bytes(20)
    digest = hmac.new(salt, pattern.encode(), hashlib.sha1).digest()
    return f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(digest).decode()}"


def pin(
    path: Path, host: str, port: int, keys: tuple[HostKey, ...]
) -> tuple[HostKey, ...]:
    """Pin `keys` for this host, or refuse if it is pinned to something else.

    Returns the keys that were newly written, so a caller can say whether it
    did anything. Hashed like ssh-keyscan -H writes them, matching what the
    shell tooling left behind - the file lists no host names in the clear.

    The read-modify-write is under an exclusive lock: this file is shared, and
    two enrolments finishing together would otherwise lose one of them.
    """
    if not keys:
        raise ValueError("refusing to pin an empty key set")

    pattern = host_pattern(host, port)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)

    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            existing_lines = handle.read().splitlines()
            existing: list[HostKey] = []
            for raw in existing_lines:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                field, _, rest = line.partition(" ")
                if not _matches(field, pattern):
                    continue
                key = HostKey.parse(rest)
                if key is not None:
                    existing.append(key)

            if existing:
                # Same host, different key: never resolved by overwriting.
                # Either it was rebuilt - then the old entry is removed
                # deliberately - or something else is answering for it.
                offered = {key.blob for key in keys}
                for pinned in existing:
                    if pinned.blob not in offered:
                        raise HostKeyMismatchError(
                            params={
                                "host": host,
                                "pinned": pinned.fingerprint,
                                "offered": ", ".join(k.fingerprint for k in keys),
                            },
                            details=(
                                f"{host} is pinned to {pinned.fingerprint} and "
                                "presented a different key"
                            ),
                        )
                # Everything already known: nothing to do, and nothing to log
                # as a change.
                known = {key.blob for key in existing}
                keys = tuple(key for key in keys if key.blob not in known)
                if not keys:
                    return ()

            addition = "".join(f"{hash_host(pattern)} {key}\n" for key in keys)
            handle.seek(0, 2)
            if existing_lines and existing_lines[-1] != "":
                handle.write("\n" if not addition.startswith("\n") else "")
            handle.write(addition)
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    path.chmod(0o600)
    return keys


def forget(path: Path, host: str, port: int) -> int:
    """Drop every entry for this host. Returns how many lines went.

    The deliberate half of the mismatch rule: a rebuilt host is un-pinned on
    purpose, by someone who decided it was rebuilt.
    """
    if not path.is_file():
        return 0
    pattern = host_pattern(host, port)

    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            kept: list[str] = []
            removed = 0
            for raw in handle.read().splitlines():
                field, _, _rest = raw.strip().partition(" ")
                if raw.strip() and not raw.startswith("#") and _matches(field, pattern):
                    removed += 1
                    continue
                kept.append(raw)
            handle.seek(0)
            handle.truncate()
            handle.write("\n".join(kept))
            if kept:
                handle.write("\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    path.chmod(0o600)
    return removed
