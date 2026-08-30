"""The two roots: shipped assets and owned runtime state.

They fall together under project_dir for local development, which is why a
test that only ever sets project_dir cannot tell whether a path was derived
from the right one. In the container they are a read-only image layer and a
volume, and getting a path from the wrong root means either a missing file or
state written somewhere no backup will find it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings

ASSET_PATHS = (
    "sensor_source_dir",
    "tool_source_dir",
    "libexec_dir",
    "template_dir",
    "http_asset_dir",
)
RUNTIME_PATHS = (
    "cert_dir",
    "private_dir",
    "credential_dir",
    "auth_user_dir",
    "probe_dir",
    "iperf_dir",
    "sensor_profile_dir",
    "backup_dir",
    "public_dir",
    "web_cert_dir",
    "ssh_key_path",
    "ssh_known_hosts_path",
)


@pytest.fixture
def environment() -> Iterator[None]:
    """Restores the process environment and the settings cache afterwards."""
    keys = (
        "PRTG_NATS_WEB_PROJECT_DIR",
        "PRTG_NATS_WEB_ASSET_DIR",
        "PRTG_NATS_WEB_RUNTIME_DIR",
    )
    previous = {key: os.environ.get(key) for key in keys}
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_one_root_by_default(tmp_path: Path) -> None:
    """Setting project_dir alone still configures the whole installation."""
    settings = Settings(project_dir=tmp_path)

    assert settings.asset_dir == tmp_path
    assert settings.runtime_dir == tmp_path / "runtime"
    assert settings.sensor_source_dir == tmp_path / "sensors"
    assert settings.tool_source_dir == tmp_path / "tools"
    assert settings.cert_dir == tmp_path / "runtime" / "certs"


def test_the_roots_can_be_moved_apart(tmp_path: Path, environment: None) -> None:
    """What the container does: assets in the image, state in a volume."""
    assets = tmp_path / "image" / "opt" / "prtg-nats"
    state = tmp_path / "volume"
    os.environ["PRTG_NATS_WEB_PROJECT_DIR"] = str(tmp_path / "unused")
    os.environ["PRTG_NATS_WEB_ASSET_DIR"] = str(assets)
    os.environ["PRTG_NATS_WEB_RUNTIME_DIR"] = str(state)
    settings = get_settings()

    for name in ASSET_PATHS:
        path = getattr(settings, name)
        assert path.is_relative_to(assets), f"{name} is not an asset path: {path}"
        assert not path.is_relative_to(state), f"{name} leaked into runtime: {path}"

    for name in RUNTIME_PATHS:
        path = getattr(settings, name)
        assert path.is_relative_to(state), f"{name} is not a runtime path: {path}"
        assert not path.is_relative_to(assets), f"{name} leaked into assets: {path}"


def test_the_database_follows_the_runtime_root(
    tmp_path: Path, environment: None
) -> None:
    """Otherwise a restore would bring back everything except the database."""
    state = tmp_path / "volume"
    os.environ["PRTG_NATS_WEB_PROJECT_DIR"] = str(tmp_path / "unused")
    os.environ["PRTG_NATS_WEB_RUNTIME_DIR"] = str(state)
    settings = get_settings()

    assert str(state / "web.db") in settings.effective_database_url


def test_backups_live_inside_the_runtime_root(tmp_path: Path) -> None:
    """One volume is the whole backup story; a sibling directory would not be."""
    settings = Settings(project_dir=tmp_path)
    assert settings.backup_dir.is_relative_to(settings.runtime_dir)


def test_overrides_are_made_absolute(tmp_path: Path, environment: None) -> None:
    os.environ["PRTG_NATS_WEB_RUNTIME_DIR"] = "relative/runtime"
    settings = get_settings()
    assert settings.runtime_dir.is_absolute()
