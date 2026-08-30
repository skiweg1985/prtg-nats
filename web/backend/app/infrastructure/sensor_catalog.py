"""The sensor catalogue, read from ``sensors/`` in the repository.

A sensor is a directory with a ``manifest.env`` and the files it ships. That is
already a versioned, declarative package format - there is nothing to invent
here, only to read.

One thing does get added: a declaration of what the sensor needs to work. The
sensor scripts define their options with argparse, an operator types them into
PRTG by hand, and whatever cannot be typed - a password, a certificate - has to
reach the probe some other way. ``parameters.json`` beside the manifest names
all of it in one place, in four kinds that differ by where the value ends up:

``parameters``
    The PRTG parameter line. Some of them are not typed at all but written as
    PRTG placeholders; ``aruba-uplink`` takes host and credentials that way.
``settings`` and ``credentials``
    ``KEY=VALUE`` lines in a profile deployed to the probe. The split is the
    protection: a credential is never read back once written.
``files``
    A certificate or a key. It travels as a file of its own and its *path*
    becomes a ``KEY=VALUE`` line, which is how the sensor scripts already
    expect to find such things.

Settings, credentials and files of one profile together are what an operator
thinks of as a variant of the sensor: one SSID, one measurement endpoint, one
site.
"""

from __future__ import annotations

import json
import re
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

FIELD_TYPES = frozenset({"string", "integer", "boolean", "choice"})
PARAMETER_SOURCES = frozenset({"manual", "prtg"})
# A profile key is written into a file the probe helper accepts only in this
# shape - see write_sensor_profile() in libexec/prtg-nats-probe-helper.
PROFILE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Certificates and keys are a few kilobytes. The ceiling exists so a mistaken
# upload fails at the door rather than on the probe.
DEFAULT_MAX_FILE_BYTES = 65536


class SchemaError(ValueError):
    """``parameters.json`` does not describe a sensor we could render."""


@dataclass(frozen=True, slots=True)
class SensorFile:
    slot: str
    source_path: Path
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ParameterField:
    """One option of a sensor, as it appears in the PRTG parameter line."""

    name: str  # "--min-download-mbit"
    type: str  # string | integer | boolean | choice
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    # English plain text, kept in step with the script's own argparse help by
    # tests/sensor-checks.py. The translation keys are optional on top: a
    # reference that shows nothing until sixty strings are translated is a
    # reference nobody can use yet.
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str | None = None
    # argparse action="append": the flag is repeated, not given a list.
    repeatable: bool = False
    # "prtg" means the value is not typed but inherited from the device or the
    # credentials for script sensors. Saying so is what keeps the interface
    # from claiming a password has to be stored on the server.
    source: str = "manual"
    prtg_placeholder: str | None = None

    @property
    def from_prtg(self) -> bool:
        return self.source == "prtg"


@dataclass(frozen=True, slots=True)
class ProfileField:
    """One ``KEY=VALUE`` line of a profile - a setting or a credential."""

    name: str  # "SSID"
    type: str
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    # Never read back, never logged, never echoed into a shared view.
    sensitive: bool = False
    # The parameter this key stands in for, so the reference can say what may
    # be left out of PRTG once a variant carries it.
    maps_to: str | None = None


@dataclass(frozen=True, slots=True)
class FileField:
    """A file that travels with a profile; its path becomes a profile key."""

    name: str  # "CA_CERT"
    kind: str = "file"  # certificate | key | file
    # Decides owner and mode on the probe, the same way the profile itself is
    # decided: a private key must not be readable by the service user unless
    # the sensor has no privileged helper to read it instead.
    secret: bool = False
    required: bool = False
    max_bytes: int = DEFAULT_MAX_FILE_BYTES
    # Part of the deployed path, and the reason the server can write that path
    # into the profile without having seen the upload.
    extension: str = ".pem"
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    maps_to: str | None = None


@dataclass(frozen=True, slots=True)
class SensorSchema:
    """What one sensor declares in ``parameters.json``."""

    parameters: tuple[ParameterField, ...] = ()
    settings: tuple[ProfileField, ...] = ()
    credentials: tuple[ProfileField, ...] = ()
    files: tuple[FileField, ...] = ()

    @property
    def supports_profiles(self) -> bool:
        """Whether variants make sense for this sensor at all.

        False for one that takes everything from PRTG - ``aruba-uplink`` does,
        deliberately - and the interface then offers no variant form.
        """
        return bool(self.settings or self.credentials or self.files)

    @property
    def profile_fields(self) -> tuple[ProfileField, ...]:
        """Settings and credentials together: every key of the profile file."""
        return self.settings + self.credentials

    def group_selector(self) -> ProfileField | None:
        """The choice setting whose options are the group names other fields use.

        wlan-auth's AUTH (psk/peap/eap-tls) is the shape this describes: a
        grouped field applies only while the selector holds its group's name,
        and "required" then means "required within that group".
        """
        groups = {field.group for field in self.profile_fields if field.group} | {
            field.group for field in self.files if field.group
        }
        if not groups:
            return None
        return next(
            (
                field
                for field in self.settings
                if field.type == "choice" and groups <= set(field.choices)
            ),
            None,
        )

    def field_applies(
        self, field: ProfileField | FileField, values: dict[str, str]
    ) -> bool:
        """Whether the field belongs to the variant these values describe."""
        if not field.group:
            return True
        selector = self.group_selector()
        if selector is None:
            return True
        active = (values.get(selector.name) or "").strip() or str(
            selector.default or ""
        )
        return field.group == active

    def profile_field(self, name: str) -> ProfileField | None:
        fields = self.profile_fields
        return next((entry for entry in fields if entry.name == name), None)

    def file_field(self, name: str) -> FileField | None:
        return next((entry for entry in self.files if entry.name == name), None)

    def parameter(self, name: str) -> ParameterField | None:
        return next((entry for entry in self.parameters if entry.name == name), None)


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
    managed_tool: str | None
    managed_tool_version: str | None
    managed_tool_fallback_min_version: str | None
    requires_privileged_helper: bool
    files: tuple[SensorFile, ...]
    schema: SensorSchema | None = None
    readme: str | None = None
    profile_template: str | None = None

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(file.slot for file in self.files)

    def file_for(self, slot: str) -> SensorFile | None:
        return next((file for file in self.files if file.slot == slot), None)

    @property
    def supports_profiles(self) -> bool:
        return self.schema is not None and self.schema.supports_profiles


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
        managed_tool = (manifest.get("SENSOR_TOOL") or "").strip() or None
        managed_tool_version = (
            manifest.get("SENSOR_TOOL_VERSION") or ""
        ).strip() or None
        managed_tool_fallback_min_version = (
            manifest.get("SENSOR_TOOL_FALLBACK_MIN_VERSION") or ""
        ).strip() or None
        if bool(managed_tool) != bool(managed_tool_version):
            raise ValueError(
                "SENSOR_TOOL and SENSOR_TOOL_VERSION must be declared together"
            )
        if managed_tool_fallback_min_version and not managed_tool:
            raise ValueError("SENSOR_TOOL_FALLBACK_MIN_VERSION requires SENSOR_TOOL")

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
            managed_tool=managed_tool,
            managed_tool_version=managed_tool_version,
            managed_tool_fallback_min_version=managed_tool_fallback_min_version,
            requires_privileged_helper=bool(
                manifest.get("SENSOR_PRIVILEGED", "").strip()
            ),
            files=tuple(files),
            schema=_read_schema(directory),
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


def _read_schema(directory: Path) -> SensorSchema | None:
    """``parameters.json`` beside the manifest, if the sensor ships one.

    Optional on purpose: a sensor without a declaration still deploys, it just
    does not get a generated form or a parameter reference. A malformed one is
    logged and dropped rather than raised - a typo in one sensor must not empty
    the whole catalogue. tests/sensor-checks.py is the strict reader; it
    compares the declaration against the script's own argparse, so the two
    cannot drift unnoticed.
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
    try:
        return parse_schema(payload)
    except SchemaError as exc:
        logger.warning(
            "sensor parameter schema is not usable",
            extra={"path": str(path), "reason": str(exc)},
        )
        return None


def parse_schema(payload: Any) -> SensorSchema:
    """Turn the parsed JSON into the schema, or say why it is not one.

    Shared with tests/sensor-checks.py through the API layer: one reader means
    a declaration the checks accept is one the platform can render.
    """
    if not isinstance(payload, dict):
        raise SchemaError("the declaration is not an object")

    schema = SensorSchema(
        parameters=tuple(
            _parse_parameter(entry) for entry in _section(payload, "parameters")
        ),
        settings=tuple(
            _parse_profile_field(entry, sensitive=False)
            for entry in _section(payload, "settings")
        ),
        credentials=tuple(
            _parse_profile_field(entry, sensitive=True)
            for entry in _section(payload, "credentials")
        ),
        files=tuple(_parse_file_field(entry) for entry in _section(payload, "files")),
    )
    _reject_duplicates(schema)
    return schema


def _section(payload: dict[str, Any], key: str) -> list[Any]:
    entries = payload.get(key, [])
    if not isinstance(entries, list):
        raise SchemaError(f"{key} is not a list")
    return entries


def _reject_duplicates(schema: SensorSchema) -> None:
    """One name, one meaning.

    A key that is both a setting and a file would have two writers for the same
    line of the profile, and the last one to run would decide.
    """
    for label, names in (
        ("parameters", [entry.name for entry in schema.parameters]),
        (
            "profile keys",
            [entry.name for entry in schema.profile_fields]
            + [entry.name for entry in schema.files],
        ),
    ):
        seen = {name for name in names if names.count(name) > 1}
        if seen:
            raise SchemaError(f"duplicate {label}: {', '.join(sorted(seen))}")


def _parse_parameter(entry: Any) -> ParameterField:
    values = _as_object(entry, "a parameter")
    name = _required_string(values, "name", "a parameter")
    if not name.startswith("-"):
        raise SchemaError(f"parameter {name!r} does not start with a dash")
    field_type = _field_type(values, name)
    source = str(values.get("source", "manual"))
    if source not in PARAMETER_SOURCES:
        raise SchemaError(f"parameter {name!r} has an unknown source {source!r}")
    placeholder = values.get("prtg_placeholder")
    if source == "prtg" and not placeholder:
        raise SchemaError(f"parameter {name!r} comes from PRTG but names none")

    return ParameterField(
        name=name,
        type=field_type,
        required=bool(values.get("required", False)),
        default=values.get("default"),
        choices=_choices(values, name),
        description=str(values.get("description", "")),
        label_key=_optional_string(values, "label_key"),
        description_key=_optional_string(values, "description_key"),
        group=_optional_string(values, "group"),
        minimum=_optional_int(values, "minimum", name),
        maximum=_optional_int(values, "maximum", name),
        placeholder=_optional_string(values, "placeholder"),
        repeatable=bool(values.get("repeatable", False)),
        source=source,
        prtg_placeholder=_optional_string(values, "prtg_placeholder"),
    )


def _parse_profile_field(entry: Any, *, sensitive: bool) -> ProfileField:
    values = _as_object(entry, "a profile key")
    name = _profile_key(values, "a profile key")
    return ProfileField(
        name=name,
        type=_field_type(values, name),
        required=bool(values.get("required", False)),
        default=values.get("default"),
        choices=_choices(values, name),
        description=str(values.get("description", "")),
        label_key=_optional_string(values, "label_key"),
        description_key=_optional_string(values, "description_key"),
        group=_optional_string(values, "group"),
        sensitive=sensitive,
        maps_to=_optional_string(values, "maps_to"),
    )


def _parse_file_field(entry: Any) -> FileField:
    values = _as_object(entry, "a file")
    name = _profile_key(values, "a file")
    extension = str(values.get("extension", ".pem"))
    # The extension is part of the deployed path, so it may not be a way to
    # walk out of the directory the helper builds.
    if not extension.startswith(".") or "/" in extension or ".." in extension:
        raise SchemaError(f"file {name!r} has an unusable extension {extension!r}")
    max_bytes = _optional_int(values, "max_bytes", name) or DEFAULT_MAX_FILE_BYTES
    if max_bytes <= 0:
        raise SchemaError(f"file {name!r} has a non-positive max_bytes")

    return FileField(
        name=name,
        kind=str(values.get("kind", "file")),
        secret=bool(values.get("secret", False)),
        required=bool(values.get("required", False)),
        max_bytes=max_bytes,
        extension=extension,
        description=str(values.get("description", "")),
        label_key=_optional_string(values, "label_key"),
        description_key=_optional_string(values, "description_key"),
        group=_optional_string(values, "group"),
        maps_to=_optional_string(values, "maps_to"),
    )


def _as_object(entry: Any, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise SchemaError(f"{label} is not an object")
    return entry


def _required_string(values: dict[str, Any], key: str, label: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{label} has no {key}")
    return value


def _optional_string(values: dict[str, Any], key: str) -> str | None:
    value = values.get(key)
    return value if isinstance(value, str) and value else None


def _optional_int(values: dict[str, Any], key: str, name: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{name!r} has a non-integer {key}")
    return value


def _field_type(values: dict[str, Any], name: str) -> str:
    field_type = str(values.get("type", "string"))
    if field_type not in FIELD_TYPES:
        raise SchemaError(f"{name!r} has an unknown type {field_type!r}")
    return field_type


def _choices(values: dict[str, Any], name: str) -> tuple[str, ...]:
    entries = values.get("choices", [])
    if not isinstance(entries, list):
        raise SchemaError(f"{name!r} has choices that are not a list")
    return tuple(str(entry) for entry in entries)


def _profile_key(values: dict[str, Any], label: str) -> str:
    name = _required_string(values, "name", label)
    if not PROFILE_KEY_PATTERN.match(name):
        raise SchemaError(
            f"{label} {name!r} is not a key the probe accepts; "
            "use upper case, digits and underscores"
        )
    return name


def _find_profile_template(directory: Path) -> str | None:
    profiles = directory / "profiles"
    if not profiles.is_dir():
        return None
    for candidate in sorted(profiles.glob("*.env.template")):
        return _read_optional_text(candidate)
    return None


def render_parameter_line(schema: SensorSchema, values: dict[str, Any]) -> str:
    """Turn form values into the parameter string PRTG expects.

    Shell-quoting is deliberately absent: the result goes into a PRTG text
    field, not into a shell. Values containing whitespace are wrapped in double
    quotes, which is what the sensors' own shlex-style splitting understands -
    with one exception, because a PRTG placeholder like ``%scriptplaceholder1``
    is substituted before the script ever sees it and must reach PRTG bare.
    """
    parts: list[str] = []

    for name, value in values.items():
        definition = schema.parameter(name)
        if definition is None or value is None or value == "":
            continue
        if definition.type == "boolean":
            if value:
                parts.append(name)
            continue
        for single in value if isinstance(value, list) else [value]:
            if single is None or single == "":
                continue
            parts.append(f"{name} {_quote(str(single))}")

    return " ".join(parts)


def default_parameter_line(schema: SensorSchema) -> str:
    """The line to start from: every required parameter, placeholders filled.

    What an operator would otherwise copy out of the sensor's README by hand.
    """
    parts: list[str] = []
    for parameter in schema.parameters:
        if parameter.from_prtg:
            parts.append(f"{parameter.name} {parameter.prtg_placeholder}")
        elif parameter.required:
            parts.append(f"{parameter.name} {parameter.placeholder or '…'}")
    return " ".join(parts)


def profile_parameter_line(schema: SensorSchema | None, profile: str) -> str:
    """What to paste into PRTG so the sensor reads this profile.

    Every sensor that reads a profile names the option --profile; if one ever
    calls it something else, its declaration is where that is said.

    A sensor without a declaration still has a profile option - the reader on
    the probe defaults to "default" whether or not anything was declared - so
    the fallback is the same answer rather than a refusal, and no caller has to
    know the rule twice.
    """
    if schema is None:
        return f"--profile {profile}"
    selector = next(
        (
            parameter.name
            for parameter in schema.parameters
            if parameter.name in ("--profile", "--variant")
        ),
        "--profile",
    )
    return f"{selector} {profile}"


def _quote(text: str) -> str:
    if text.startswith("%"):
        return text
    return f'"{text}"' if any(character.isspace() for character in text) else text
