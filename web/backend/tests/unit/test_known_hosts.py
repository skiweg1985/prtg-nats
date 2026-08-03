"""Pinning, and the one rule that makes it worth anything.

A host that is not pinned yet gets pinned. A host that is pinned and presents
something else is an error, never an overwrite - otherwise the file records
whatever answered last, which is the opposite of pinning.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.errors import HostKeyMismatchError
from app.infrastructure import known_hosts as kh

_KEYGEN = shutil.which("ssh-keygen") or "ssh-keygen"


def _key(seed: bytes) -> kh.HostKey:
    """An Ed25519 host key blob: the algorithm name, then 32 bytes of key.

    Built rather than pasted so it is valid base64 and decodes to something
    the fingerprint can be taken of - HostKey.parse rejects anything else,
    which is what a truncated line in a real file looks like.
    """
    body = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + seed.ljust(32, b"\x00")
    return kh.HostKey("ssh-ed25519", base64.b64encode(body).decode())


KEY_A = _key(b"host-a")
KEY_B = _key(b"host-b")


def test_a_new_host_is_pinned_to_what_it_reported(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"

    written = kh.pin(path, "probe.example.test", 22, (KEY_A,))

    assert written == (KEY_A,)
    assert kh.read_pinned(path, "probe.example.test", 22) == (KEY_A,)
    assert path.stat().st_mode & 0o077 == 0


def test_pinning_the_same_key_again_changes_nothing(tmp_path: Path) -> None:
    """Re-enrolling a probe that was not rebuilt is not an event."""
    path = tmp_path / "known_hosts"
    kh.pin(path, "probe.example.test", 22, (KEY_A,))
    before = path.read_text(encoding="utf-8")

    assert kh.pin(path, "probe.example.test", 22, (KEY_A,)) == ()
    assert path.read_text(encoding="utf-8") == before


def test_a_different_key_for_a_pinned_host_is_refused(tmp_path: Path) -> None:
    """The whole reason the file exists."""
    path = tmp_path / "known_hosts"
    kh.pin(path, "probe.example.test", 22, (KEY_A,))

    with pytest.raises(HostKeyMismatchError) as raised:
        kh.pin(path, "probe.example.test", 22, (KEY_B,))

    # The message names both fingerprints, so an operator can tell a rebuild
    # from an impersonation without going to look.
    assert KEY_A.fingerprint in str(raised.value.params.values())
    assert KEY_B.fingerprint in str(raised.value.params.values())
    # And the file still holds the original.
    assert kh.read_pinned(path, "probe.example.test", 22) == (KEY_A,)


def test_forgetting_a_host_is_the_deliberate_way_back(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    kh.pin(path, "probe.example.test", 22, (KEY_A,))
    kh.pin(path, "other.example.test", 22, (KEY_B,))

    assert kh.forget(path, "probe.example.test", 22) == 1

    assert kh.read_pinned(path, "probe.example.test", 22) == ()
    # The neighbour is untouched.
    assert kh.read_pinned(path, "other.example.test", 22) == (KEY_B,)
    # And now the rebuilt host can be pinned to its new key.
    assert kh.pin(path, "probe.example.test", 22, (KEY_B,)) == (KEY_B,)


def test_a_nonstandard_port_is_a_different_host(tmp_path: Path) -> None:
    """OpenSSH spells it [host]:port, and so does this."""
    path = tmp_path / "known_hosts"
    kh.pin(path, "probe.example.test", 2222, (KEY_A,))

    assert kh.read_pinned(path, "probe.example.test", 2222) == (KEY_A,)
    assert kh.read_pinned(path, "probe.example.test", 22) == ()
    assert "[probe.example.test]:2222" not in path.read_text(encoding="utf-8")


def test_entries_written_by_the_shell_tooling_are_recognised(tmp_path: Path) -> None:
    """An installation that predates this module has hashed entries.

    If they read as unpinned, every existing probe would look like a new host
    and the mismatch rule would never fire.
    """
    path = tmp_path / "known_hosts"
    pattern = kh.host_pattern("probe.example.test", 22)
    path.write_text(f"{kh.hash_host(pattern)} {KEY_A}\n", encoding="utf-8")

    assert kh.read_pinned(path, "probe.example.test", 22) == (KEY_A,)
    with pytest.raises(HostKeyMismatchError):
        kh.pin(path, "probe.example.test", 22, (KEY_B,))


def test_the_file_lists_no_host_names_in_the_clear(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    kh.pin(path, "probe.example.test", 22, (KEY_A,))

    assert "probe.example.test" not in path.read_text(encoding="utf-8")


def test_an_empty_key_set_is_refused(tmp_path: Path) -> None:
    """Pinning nothing would read as success and leave the host unpinned."""
    with pytest.raises(ValueError):
        kh.pin(tmp_path / "known_hosts", "probe.example.test", 22, ())


@pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen is not installed"
)
def test_the_fingerprint_matches_ssh_keygen(tmp_path: Path) -> None:
    """The string an operator compares has to be the one their tools print.

    Compared against the real ssh-keygen rather than a fixture: the point is
    that the two agree, and a fixture would only prove this module agrees with
    itself. Hence the subprocess calls - fixed arguments, no caller input.
    """
    generated = tmp_path / "hostkey"
    subprocess.run(  # noqa: S603
        [_KEYGEN, "-t", "ed25519", "-N", "", "-f", str(generated), "-q"],
        check=True,
    )
    public = (tmp_path / "hostkey.pub").read_text(encoding="utf-8")
    expected = subprocess.run(  # noqa: S603
        [_KEYGEN, "-lf", str(tmp_path / "hostkey.pub")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[1]

    key = kh.HostKey.parse(public)
    assert key is not None
    assert key.fingerprint == expected


def test_a_malformed_line_is_ignored_rather_than_crashing(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    path.write_text(
        f"# a comment\n\nnot-a-real-line\nprobe.example.test {KEY_A}\n",
        encoding="utf-8",
    )

    assert kh.read_pinned(path, "probe.example.test", 22) == (KEY_A,)
