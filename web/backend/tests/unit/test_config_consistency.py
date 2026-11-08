"""Configurations the API refuses to start with."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings


def test_the_probe_channel_cannot_share_the_interface_port(tmp_path: Path) -> None:
    # One port cannot both demand a client certificate and serve a browser
    # that has none; the proxy would fail to start with a less helpful error.
    settings = Settings(
        project_dir=tmp_path, WEB_HTTPS_PORT=8443, PROBE_CHANNEL_PORT=8443
    )
    with pytest.raises(RuntimeError, match="PROBE_CHANNEL_PORT"):
        settings.check_consistency()


def test_distinct_ports_are_accepted(tmp_path: Path) -> None:
    Settings(
        project_dir=tmp_path, WEB_HTTPS_PORT=9443, PROBE_CHANNEL_PORT=8443
    ).check_consistency()
