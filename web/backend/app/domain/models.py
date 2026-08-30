"""Domain values shared across services.

Plain dataclasses, no ORM and no HTTP. They are what a service returns and what
the reconciliation diff compares, which keeps both testable without a database
and without a probe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import (
    CaState,
    DeviationKind,
    DeviationSeverity,
    NatsConnectionState,
    ProbeStatus,
    ServiceState,
)
from app.infrastructure.probe_helper import (
    CURRENT_HELPER_VERSION,
    HelperResponse,
    normalise_optional,
)


@dataclass(frozen=True, slots=True)
class InstalledSensor:
    """One entry of the probe's own ``sensor-list`` answer."""

    name: str
    version: str | None
    sha256: str | None
    interfaces: tuple[str, ...] = ()
    helper_state: str | None = None
    # Absent from a helper below version 6. None therefore means "not
    # reported", never "no helper" - that is what helper_state says.
    helper_sha256: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    tool_platform: str | None = None
    tool_sha256: str | None = None
    tool_source: str | None = None
    tool_path: str | None = None
    tool_compatible: bool | None = None


@dataclass(frozen=True, slots=True)
class WirelessInterface:
    """One radio interface of a probe, as ``wireless-interfaces`` reports it.

    Everything here is a fact the probe stated, not a verdict. Whether an
    interface may be reserved is decided on the probe when it is asked to do
    it; this only carries what somebody needs in order to ask.
    """

    name: str
    reserved_by: str | None = None
    carries_default_route: bool = False
    operstate: str | None = None
    nm_state: str | None = None
    connection: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedProbeState:
    """What a probe reported, and when.

    ``observed_at`` is not decoration: the probe list shows cached values, and
    an operator has to be able to tell a healthy probe from one that was
    healthy twenty minutes ago.
    """

    nats_username: str
    observed_at: datetime
    reachable: bool
    service: ServiceState = ServiceState.UNKNOWN
    package_version: str | None = None
    hostname: str | None = None
    ca_sha256: str | None = None
    config_path: str | None = None
    probe_id: str | None = None
    probe_name: str | None = None
    has_access_key: bool = False
    # None for a helper from before it reported its version, which is exactly
    # the probe that cannot be updated over the channel yet.
    helper_version: int | None = None
    helper_sha256: str | None = None
    platform: str | None = None
    sensors: tuple[InstalledSensor, ...] = ()
    error_code: str | None = None
    error_details: str | None = None

    @property
    def helper_outdated(self) -> bool:
        """Whether this probe is behind what the platform expects of it.

        An unreachable probe is not called outdated: nothing was reported, and
        guessing from silence would put a warning on every probe that happens
        to be down.
        """
        if not self.reachable:
            return False
        if self.helper_version is None:
            return True
        return self.helper_version < CURRENT_HELPER_VERSION

    def ca_state(self, expected_sha256: str | None) -> CaState:
        if not self.reachable:
            return CaState.UNKNOWN
        if self.ca_sha256 is None:
            return CaState.MISSING
        if expected_sha256 is None:
            return CaState.UNKNOWN
        return CaState.OK if self.ca_sha256 == expected_sha256 else CaState.MISMATCHED

    def sensor(self, name: str) -> InstalledSensor | None:
        return next((sensor for sensor in self.sensors if sensor.name == name), None)

    def to_document(self) -> dict[str, Any]:
        """The JSON form stored in probe_observed_state.document."""
        return {
            "service": self.service.value,
            "package_version": self.package_version,
            "hostname": self.hostname,
            "ca_sha256": self.ca_sha256,
            "config_path": self.config_path,
            "probe_id": self.probe_id,
            "probe_name": self.probe_name,
            "has_access_key": self.has_access_key,
            "helper_version": self.helper_version,
            "helper_sha256": self.helper_sha256,
            "platform": self.platform,
            "sensors": [
                {
                    "name": sensor.name,
                    "version": sensor.version,
                    "sha256": sensor.sha256,
                    "interfaces": list(sensor.interfaces),
                    "helper_state": sensor.helper_state,
                    "helper_sha256": sensor.helper_sha256,
                    "tool_name": sensor.tool_name,
                    "tool_version": sensor.tool_version,
                    "tool_platform": sensor.tool_platform,
                    "tool_sha256": sensor.tool_sha256,
                    "tool_source": sensor.tool_source,
                    "tool_path": sensor.tool_path,
                    "tool_compatible": sensor.tool_compatible,
                }
                for sensor in self.sensors
            ],
        }

    @classmethod
    def from_document(
        cls,
        *,
        nats_username: str,
        observed_at: datetime,
        reachable: bool,
        document: dict[str, Any],
        error_code: str | None = None,
        error_details: str | None = None,
    ) -> ObservedProbeState:
        return cls(
            nats_username=nats_username,
            observed_at=observed_at,
            reachable=reachable,
            service=_service_state(document.get("service")),
            package_version=document.get("package_version"),
            hostname=document.get("hostname"),
            ca_sha256=document.get("ca_sha256"),
            config_path=document.get("config_path"),
            probe_id=document.get("probe_id"),
            probe_name=document.get("probe_name"),
            has_access_key=bool(document.get("has_access_key")),
            helper_version=document.get("helper_version"),
            helper_sha256=document.get("helper_sha256"),
            platform=document.get("platform"),
            sensors=tuple(
                InstalledSensor(
                    name=entry.get("name", ""),
                    version=entry.get("version"),
                    sha256=entry.get("sha256"),
                    interfaces=tuple(entry.get("interfaces") or ()),
                    helper_state=entry.get("helper_state"),
                    helper_sha256=entry.get("helper_sha256"),
                    tool_name=entry.get("tool_name"),
                    tool_version=entry.get("tool_version"),
                    tool_platform=entry.get("tool_platform"),
                    tool_sha256=entry.get("tool_sha256"),
                    tool_source=entry.get("tool_source"),
                    tool_path=entry.get("tool_path"),
                    tool_compatible=entry.get("tool_compatible"),
                )
                for entry in document.get("sensors", [])
            ),
            error_code=error_code,
            error_details=error_details,
        )


def parse_probe_info(
    nats_username: str, response: HelperResponse, observed_at: datetime
) -> ObservedProbeState:
    """Turn a ``probe-info`` answer into domain state."""
    return ObservedProbeState(
        nats_username=nats_username,
        observed_at=observed_at,
        reachable=True,
        service=_service_state(response.value("service")),
        package_version=normalise_optional(response.value("package")),
        hostname=normalise_optional(response.value("hostname")),
        ca_sha256=normalise_optional(response.value("ca_sha256")),
        config_path=normalise_optional(response.value("config")),
        probe_id=normalise_optional(response.value("id")),
        probe_name=normalise_optional(response.value("name")),
        has_access_key=normalise_optional(response.value("access_key")) is not None,
        helper_version=_helper_version(response.value("helper_version")),
        helper_sha256=normalise_optional(response.value("helper_sha256")),
        platform=normalise_optional(response.value("platform")),
    )


def _helper_version(value: str | None) -> int | None:
    """None for anything that is not a number, including a missing field.

    Both mean the same thing here - the probe did not name a version we can
    compare - and neither is worth failing a status read over.
    """
    text = normalise_optional(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def parse_sensor_list(response: HelperResponse) -> tuple[InstalledSensor, ...]:
    sensors = []
    for record in response.records:
        interfaces = normalise_optional(record.get("interfaces"))
        sensors.append(
            InstalledSensor(
                name=record["name"],
                version=normalise_optional(record.get("version")),
                sha256=normalise_optional(record.get("sha256")),
                interfaces=tuple(interfaces.split(",")) if interfaces else (),
                helper_state=normalise_optional(record.get("helper")),
                helper_sha256=normalise_optional(record.get("helper_sha256")),
                tool_name=normalise_optional(record.get("tool")),
                tool_version=normalise_optional(record.get("tool_version")),
                tool_platform=normalise_optional(record.get("tool_platform")),
                tool_sha256=normalise_optional(record.get("tool_sha256")),
                tool_source=normalise_optional(record.get("tool_source")),
                tool_path=normalise_optional(record.get("tool_path")),
                tool_compatible=(
                    record.get("tool_compatible") == "yes"
                    if normalise_optional(record.get("tool_compatible")) is not None
                    else None
                ),
            )
        )
    return tuple(sensors)


def parse_wireless_interfaces(
    response: HelperResponse,
) -> tuple[WirelessInterface, ...]:
    interfaces = []
    for record in response.records:
        reserved = normalise_optional(record.get("reserved"))
        connection = normalise_optional(record.get("connection"))
        interfaces.append(
            WirelessInterface(
                name=record["name"],
                # "none" is the helper's way of writing an empty field in a
                # tab-separated line; it is not a sensor called none.
                reserved_by=None if reserved in (None, "none") else reserved,
                carries_default_route=record.get("default_route") == "yes",
                operstate=normalise_optional(record.get("operstate")),
                nm_state=normalise_optional(record.get("nm_state")),
                connection=None if connection in (None, "none") else connection,
            )
        )
    return tuple(interfaces)


def _service_state(value: str | None) -> ServiceState:
    match (value or "").strip().lower():
        case "active":
            return ServiceState.ACTIVE
        case "inactive":
            return ServiceState.INACTIVE
        case _:
            return ServiceState.UNKNOWN


@dataclass(frozen=True, slots=True)
class DesiredSensor:
    name: str
    version: str | None = None  # None means "whatever the catalogue ships"
    profiles: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DesiredProbeState:
    sensors: tuple[DesiredSensor, ...] = ()
    probe_name: str | None = None
    ca_required: bool = True

    def to_document(self) -> dict[str, Any]:
        return {
            "sensors": [
                {
                    "name": sensor.name,
                    "version": sensor.version,
                    "profiles": list(sensor.profiles),
                    "interfaces": list(sensor.interfaces),
                }
                for sensor in self.sensors
            ],
            "probe_name": self.probe_name,
            "ca_required": self.ca_required,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> DesiredProbeState:
        return cls(
            sensors=tuple(
                DesiredSensor(
                    name=entry["name"],
                    version=entry.get("version"),
                    profiles=tuple(entry.get("profiles") or ()),
                    interfaces=tuple(entry.get("interfaces") or ()),
                )
                for entry in document.get("sensors", [])
                if entry.get("name")
            ),
            probe_name=document.get("probe_name"),
            ca_required=bool(document.get("ca_required", True)),
        )


@dataclass(frozen=True, slots=True)
class Deviation:
    """One difference between wanted and actual, with the fix attached."""

    kind: DeviationKind
    severity: DeviationSeverity
    object_type: str
    object_ref: str
    expected: str | None = None
    actual: str | None = None
    # The action that resolves it, e.g. "sensor.deploy". The interface turns
    # this into the button on "fix deviations".
    remediation: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProbeSummary:
    """One row of the probe table."""

    id: str
    nats_username: str
    display_name: str | None
    host: str
    probe_name: str | None
    status: ProbeStatus
    service: ServiceState
    package_version: str | None
    ca_state: CaState
    nats_connection: NatsConnectionState
    sensor_count: int
    deviation_count: int
    observed_at: datetime | None
    stale: bool
    running_job_id: str | None = None
    error_code: str | None = None
    helper_version: int | None = None
    # In the row rather than only on the detail page: an operator should see
    # which probes will refuse the next job before starting it.
    helper_outdated: bool = False
    # The operator's tick that the access key was entered in PRTG and the
    # probe approved there. Not part of the status: a probe without it is
    # healthy here and invisible over there, which is exactly why it needs
    # its own signal.
    prtg_registered: bool = False
