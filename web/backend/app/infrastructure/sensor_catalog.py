"""The sensor catalogue, read from ``sensors/`` in the repository.

A sensor is a directory with a ``manifest.env`` and the files it ships. That is
already a versioned, declarative package format - there is nothing to invent
here, only to read.

One thing does get added: a parameter schema. The sensor scripts define their
options with argparse and an operator types them into PRTG by hand. A schema
next to the manifest lets the interface render a form and produce the exact
parameter line to paste, instead of leaving the operator to guess flag names
from a README.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.infrastructure.runtime_files import NAME_PATTERN, read_env_file

logger = get_logger(__name__)

# Manifest key -> the helper slot the file is staged into.
SLOT_MANIFEST_KEYS: dict[str, str] = {
    "script": "SENSOR_SCRIPT",
    "wrapper": "SENSOR_PRIVILEGED",
    "requirements": "SENSOR_REQUIREMENTS",
}


@dataclass(frozen=True, slots=True)
class SensorFile:
    slot: str
    source_path: Path
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SensorDefinition:
    name: str
    version: str
    description: str
    directory: Path
    needs_interface: bool
    # Set when the sensor measures against an endpoint managed by
    # "./prtg-nats iperf-server"; its credentials ship with the sensor.
    iperf_kind: str | None
    requires_privileged_helper: bool
    files: tuple[SensorFile, ...]
    parameter_schema: dict[str, Any] | None = None
    readme: str | None = None
    profile_template: str | None = None

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(file.slot for file in self.files)

    def file_for(self, slot: str) -> SensorFile | None:
        return next((file for file in self.files if file.slot == slot), None)


@dataclass(frozen=True, slots=True)
class ParameterField:
    """One option of a sensor, in the form the interface renders."""

    name: str  # "--min-download-mbit"
    type: str  # string | integer | boolean | choice
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    # Translation key for the label; falls back to the flag name.
    label_key: str | None = None
    description_key: str | None = None
    # Marked so the interface never echoes the value back into a shared view.
    sensitive: bool = False
    group: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str | None = None


class SensorCatalog:
    """Reads ``sensors/`` on demand.

    No cache: the directory is a handful of files, an operator who edits a
    manifest expects the change to show, and a stale catalogue would deploy the
    wrong version.
    """

    def __init__(self, source_dir: Path) -> None:
        self._source_dir = source_dir

    def list(self) -> list[SensorDefinition]:
        if not self._source_dir.is_dir():
            return []
        definitions = []
        for directory in sorted(self._source_dir.iterdir()):
            if not directory.is_dir() or not (directory / "manifest.env").is_file():
                continue
            try:
                definitions.append(self._load(directory))
            except (OSError, ValueError) as exc:
                logger.warning(
                    "skipping unreadable sensor",
                    extra={"sensor": directory.name, "reason": str(exc)},
                )
        return definitions

    def get(self, name: str) -> SensorDefinition:
        if not NAME_PATTERN.match(name):
            raise NotFoundError.of("sensor", name)
        directory = self._source_dir / name
        if not (directory / "manifest.env").is_file():
            raise NotFoundError.of("sensor", name)
        return self._load(directory)

    def _load(self, directory: Path) -> SensorDefinition:
        manifest = read_env_file(directory / "manifest.env")
        name = manifest.get("SENSOR_NAME", directory.name)

        files: list[SensorFile] = []
        for slot, key in SLOT_MANIFEST_KEYS.items():
            relative = manifest.get(key, "").strip()
            if not relative:
                continue
            source = directory / relative
            if not source.is_file():
                logger.warning(
                    "sensor manifest points at a missing file",
                    extra={"sensor": name, "slot": slot, "path": str(source)},
                )
                continue
            payload = source.read_bytes()
            files.append(
                SensorFile(
                    slot=slot,
                    source_path=source,
                    relative_path=relative,
                    size_bytes=len(payload),
                    sha256=_sha256(payload),
                )
            )

        return SensorDefinition(
            name=name,
            version=manifest.get("SENSOR_VERSION", "0").strip(),
            description=manifest.get("SENSOR_DESCRIPTION", "").strip(),
            directory=directory,
            needs_interface=_as_bool(manifest.get("SENSOR_NEEDS_INTERFACE")),
            iperf_kind=(manifest.get("SENSOR_IPERF") or "").strip() or None,
            requires_privileged_helper=bool(
                manifest.get("SENSOR_PRIVILEGED", "").strip()
            ),
            files=tuple(files),
            parameter_schema=_read_parameter_schema(directory),
            readme=_read_optional_text(directory / "README.md"),
            profile_template=_find_profile_template(directory),
        )

    def read_slot(self, definition: SensorDefinition, slot: str) -> str:
        """The content that goes onto the probe for one slot.

        ``version`` is generated rather than stored: it is the manifest version
        and must never disagree with it.
        """
        if slot == "version":
            return f"{definition.version}\n"
        sensor_file = definition.file_for(slot)
        if sensor_file is None:
            raise NotFoundError.of("sensor_file", f"{definition.name}/{slot}")
        return sensor_file.source_path.read_text(encoding="utf-8")


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "yes", "1"}


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_parameter_schema(directory: Path) -> dict[str, Any] | None:
    """``parameters.json`` beside the manifest, if the sensor ships one.

    Optional on purpose: a sensor without a schema still deploys, it just does
    not get a generated form. tests/sensor-checks.py compares the schema
    against the script's own argparse definition, so the two cannot drift.
    """
    path = directory / "parameters.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "sensor parameter schema is not valid JSON",
            extra={"path": str(path), "reason": str(exc)},
        )
        return None
    return payload if isinstance(payload, dict) else None


def _find_profile_template(directory: Path) -> str | None:
    profiles = directory / "profiles"
    if not profiles.is_dir():
        return None
    for candidate in sorted(profiles.glob("*.env.template")):
        return _read_optional_text(candidate)
    return None


def render_parameter_line(schema: dict[str, Any], values: dict[str, Any]) -> str:
    """Turn form values into the parameter string PRTG expects.

    Shell-quoting is deliberately absent: the result goes into a PRTG text
    field, not into a shell. Values containing whitespace are wrapped in double
    quotes, which is what the sensors' own shlex-style splitting understands.
    """
    fields = {field["name"]: field for field in schema.get("fields", [])}
    parts: list[str] = []

    for name, value in values.items():
        definition = fields.get(name)
        if definition is None or value is None or value == "":
            continue
        if definition.get("type") == "boolean":
            if value:
                parts.append(name)
            continue
        text = str(value)
        if any(character.isspace() for character in text):
            text = f'"{text}"'
        parts.append(f"{name} {text}")

    return " ".join(parts)
