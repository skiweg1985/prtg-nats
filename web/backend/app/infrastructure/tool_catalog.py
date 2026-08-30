"""Release-owned executables used by sensors.

The catalogue is the only place that turns a probe platform into bytes. A
caller names a known tool and the platform reported by the probe; it never
gets to provide a path, package name or download URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from app.core.errors import RuntimeStateError

FORMAT_VERSION = 1
ENVELOPE_HEADER = "PRTG-NATS-MANAGED-TOOL-V1"
MANAGED_TOOL_PLATFORMS: dict[str, frozenset[str]] = {
    "iperf3": frozenset(
        {
            "linux-amd64-glibc",
            "linux-arm64-glibc",
            "linux-armhf-glibc",
        }
    )
}
SYSTEM_TOOL_MINIMUM_VERSIONS: dict[str, str] = {"iperf3": "3.18"}
KNOWN_LINUX_PLATFORM_PATTERN = re.compile(r"^linux-[a-z0-9][a-z0-9_-]*-(?:glibc|musl)$")


@dataclass(frozen=True, slots=True)
class ToolArtifact:
    name: str
    version: str
    platform: str
    path: Path
    sha256: str
    size: int

    def read_verified(self) -> bytes:
        """Read the artifact and prove it still matches the release manifest."""
        try:
            payload = self.path.read_bytes()
        except OSError as exc:
            raise RuntimeStateError(
                params={"path": str(self.path)},
                details="the managed tool artifact is missing",
            ) from exc
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if len(payload) != self.size or actual_sha256 != self.sha256:
            raise RuntimeStateError(
                params={"path": str(self.path)},
                details=(
                    "the managed tool artifact does not match its release manifest"
                ),
            )
        return payload


class ToolCatalog:
    """Resolve a release-owned tool for one exact userspace platform."""

    def __init__(self, source_dir: Path) -> None:
        self._source_dir = source_dir

    def select(self, name: str, platform: str) -> ToolArtifact:
        manifest_path, version, artifacts = self._definition(name)
        if platform not in artifacts:
            raise RuntimeStateError(
                params={"path": str(manifest_path)},
                details=(f"no approved {name} artifact exists for platform {platform}"),
            )
        entry = artifacts[platform]
        if not isinstance(entry, dict):
            self._invalid(manifest_path, f"artifact {platform} is malformed")
        relative = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or size <= 0
        ):
            self._invalid(manifest_path, f"artifact {platform} is malformed")
        artifact_path = (manifest_path.parent / relative).resolve()
        try:
            artifact_path.relative_to(manifest_path.parent.resolve())
        except ValueError:
            self._invalid(manifest_path, f"artifact {platform} escapes its directory")
        return ToolArtifact(
            name=name,
            version=version,
            platform=platform,
            path=artifact_path,
            sha256=digest,
            size=size,
        )

    def version(self, name: str) -> str:
        """Return the release version even when a platform is unsupported."""
        _, version, _ = self._definition(name)
        return version

    def has_managed_artifact(self, name: str, platform: str) -> bool:
        """Whether policy requires release-owned bytes for this platform."""
        self._definition(name)
        platforms = MANAGED_TOOL_PLATFORMS.get(name)
        if platforms is None:
            raise RuntimeStateError(
                params={"path": str(self._source_dir)},
                details=f"managed tool {name} has no platform policy",
            )
        return platform in platforms

    def validate_system_fallback(self, name: str, platform: str) -> None:
        """Reject fallback as an escape hatch for a managed or unknown ABI."""
        if self.has_managed_artifact(name, platform):
            raise RuntimeStateError(
                params={"path": str(self._source_dir)},
                details=(f"platform {platform} requires the managed {name} artifact"),
            )
        if "unknown" in platform or not KNOWN_LINUX_PLATFORM_PATTERN.fullmatch(
            platform
        ):
            raise RuntimeStateError(
                params={"path": str(self._source_dir)},
                details=(f"platform {platform} cannot use the {name} system fallback"),
            )

    def system_fallback_minimum(self, name: str) -> str:
        """Return the release policy floor used by both deploy entry points."""
        self._definition(name)
        try:
            return SYSTEM_TOOL_MINIMUM_VERSIONS[name]
        except KeyError as exc:
            raise RuntimeStateError(
                params={"path": str(self._source_dir)},
                details=f"managed tool {name} has no system fallback policy",
            ) from exc

    def _definition(self, name: str) -> tuple[Path, str, dict[str, Any]]:
        manifest_path = self._source_dir / name / "artifacts" / "manifest.json"
        document = self._read_manifest(manifest_path)
        if document.get("format") != FORMAT_VERSION:
            self._invalid(manifest_path, "unsupported managed tool manifest format")
        raw_tools = document.get("tools")
        if not isinstance(raw_tools, dict) or name not in raw_tools:
            self._invalid(manifest_path, f"managed tool {name} is not declared")
        tools = cast(dict[str, Any], raw_tools)
        definition = tools[name]
        if not isinstance(definition, dict):
            self._invalid(manifest_path, f"managed tool {name} is malformed")
        version = definition.get("version")
        artifacts = definition.get("artifacts")
        if not isinstance(version, str) or not version:
            self._invalid(manifest_path, f"managed tool {name} has no version")
        if not isinstance(artifacts, dict):
            self._invalid(manifest_path, f"managed tool {name} has no artifacts")
        return manifest_path, version, cast(dict[str, Any], artifacts)

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeStateError(
                params={"path": str(path)},
                details="the managed tool manifest is missing or invalid",
            ) from exc
        if not isinstance(document, dict):
            ToolCatalog._invalid(path, "managed tool manifest is not an object")
        return cast(dict[str, Any], document)

    @staticmethod
    def _invalid(path: Path, details: str) -> None:
        raise RuntimeStateError(params={"path": str(path)}, details=details)


def build_tool_envelope(artifact: ToolArtifact) -> str:
    """Return the exact signed text the helper validates and decodes."""
    payload = artifact.read_verified()
    encoded = base64.b64encode(payload).decode("ascii")
    return (
        f"{ENVELOPE_HEADER}\n"
        f"tool={artifact.name}\n"
        f"version={artifact.version}\n"
        f"platform={artifact.platform}\n"
        f"sha256={artifact.sha256}\n"
        f"size={artifact.size}\n"
        "\n"
        f"{encoded}\n"
    )
