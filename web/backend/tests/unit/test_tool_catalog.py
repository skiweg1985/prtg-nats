from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from app.core.errors import RuntimeStateError
from app.infrastructure.tool_catalog import ToolCatalog, build_tool_envelope
from tests.conftest import write_tool_artifact

PLATFORM = "linux-arm64-glibc"


def test_selects_and_envelopes_the_exact_platform(tmp_path: Path) -> None:
    payload = b"approved executable\n"
    write_tool_artifact(tmp_path, "iperf3", PLATFORM, payload)

    artifact = ToolCatalog(tmp_path / "tools").select("iperf3", PLATFORM)
    envelope = build_tool_envelope(artifact)

    assert artifact.version == "3.21"
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert f"platform={PLATFORM}\n" in envelope
    assert f"size={len(payload)}\n" in envelope
    assert base64.b64encode(payload).decode("ascii") in envelope


def test_unknown_platform_fails_closed(tmp_path: Path) -> None:
    write_tool_artifact(tmp_path, "iperf3", PLATFORM, b"approved")

    with pytest.raises(RuntimeStateError) as error:
        ToolCatalog(tmp_path / "tools").select("iperf3", "linux-armhf-glibc")
    assert "no approved iperf3 artifact" in (error.value.details or "")


def test_changed_artifact_is_rejected_before_transfer(tmp_path: Path) -> None:
    path = write_tool_artifact(tmp_path, "iperf3", PLATFORM, b"approved")
    artifact = ToolCatalog(tmp_path / "tools").select("iperf3", PLATFORM)
    path.write_bytes(b"changed")

    with pytest.raises(RuntimeStateError) as error:
        build_tool_envelope(artifact)
    assert "does not match" in (error.value.details or "")


def test_release_policy_keeps_supported_platforms_on_managed_bytes(
    tmp_path: Path,
) -> None:
    write_tool_artifact(tmp_path, "iperf3", PLATFORM, b"approved")
    catalog = ToolCatalog(tmp_path / "tools")

    assert catalog.has_managed_artifact("iperf3", "linux-amd64-glibc")
    assert catalog.has_managed_artifact("iperf3", "linux-arm64-glibc")
    assert catalog.has_managed_artifact("iperf3", "linux-armhf-glibc")
    with pytest.raises(RuntimeStateError) as error:
        catalog.validate_system_fallback("iperf3", "linux-arm64-glibc")
    assert "requires the managed" in (error.value.details or "")


def test_known_unmanaged_linux_platform_uses_system_floor(tmp_path: Path) -> None:
    write_tool_artifact(tmp_path, "iperf3", PLATFORM, b"approved")
    catalog = ToolCatalog(tmp_path / "tools")

    catalog.validate_system_fallback("iperf3", "linux-riscv64-glibc")
    catalog.validate_system_fallback("iperf3", "linux-armhf-v6-glibc")
    assert catalog.system_fallback_minimum("iperf3") == "3.18"


@pytest.mark.parametrize(
    "platform",
    ("linux-unknown-glibc", "darwin-arm64", "linux-arm64-other", "linux-/glibc"),
)
def test_unknown_or_malformed_platform_has_no_system_fallback(
    tmp_path: Path, platform: str
) -> None:
    write_tool_artifact(tmp_path, "iperf3", PLATFORM, b"approved")

    with pytest.raises(RuntimeStateError) as error:
        ToolCatalog(tmp_path / "tools").validate_system_fallback("iperf3", platform)
    assert "cannot use" in (error.value.details or "")
