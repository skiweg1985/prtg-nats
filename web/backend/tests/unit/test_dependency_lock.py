"""The image installs exactly the versions the lockfile names.

pyproject.toml states lower bounds only. Without a lock, two builds of the
same commit could carry different libraries, and a scan of one says nothing
about the other.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec)
    assert match, spec
    return _normalise(match.group(0))


def _pins(lockfile: Path) -> dict[str, str]:
    """Every pinned package in a hashed requirements file, name to version."""
    text = lockfile.read_text(encoding="utf-8")
    pins: dict[str, str] = {}
    for block in re.split(r"\n(?=\S)", text):
        first = block.splitlines()[0]
        if first.startswith("#") or not first.strip():
            continue
        match = PIN.match(first)
        assert match, f"{lockfile.name}: not an exact pin: {first}"
        assert "--hash=sha256:" in block, f"{lockfile.name}: no hash for {first}"
        pins[_normalise(match.group(1))] = match.group(3)
    return pins


def _project() -> dict[str, list[str]]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    return {
        "runtime": project["dependencies"],
        "dev": project["optional-dependencies"]["dev"],
    }


def test_every_runtime_dependency_is_pinned_with_a_hash() -> None:
    pins = _pins(BACKEND / "requirements.lock")
    missing = [
        spec for spec in _project()["runtime"] if _requirement_name(spec) not in pins
    ]
    assert not missing, f"requirements.lock lacks {missing}; re-run uv pip compile"


def test_the_development_lock_carries_the_runtime_versions_too() -> None:
    """Tests run against what the image ships, not a newer resolution."""
    runtime = _pins(BACKEND / "requirements.lock")
    dev = _pins(BACKEND / "requirements-dev.lock")
    missing = [spec for spec in _project()["dev"] if _requirement_name(spec) not in dev]
    assert not missing, f"requirements-dev.lock lacks {missing}"
    drifted = {
        name: (version, dev.get(name))
        for name, version in runtime.items()
        if dev.get(name) != version
    }
    assert not drifted, f"the two lockfiles disagree: {drifted}"


def test_the_image_installs_from_the_lock() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in dockerfile
    # Installing the project would resolve or build outside the lock.
    assert not re.search(r"pip install[^\n]*\s\.(\s|$)", dockerfile)
