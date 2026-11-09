"""Domain values shared across services.

Plain dataclasses, no ORM and no HTTP. They are what a service returns and what
the reconciliation diff compares, which keeps both testable without a database
and without a probe.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

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
class SensorJob:
    """One job a sensor's privileged helper measures, as ``sensor-jobs`` says.

    ``label`` is the helper's name for the configuration without its
    credentials - the same value a sensor log record carries as ``job``.
    """

    label: str
    interface: str | None = None
    ssid: str | None = None
    # What tells two configurations of one SSID apart: the method, the
    # variant (the sensor's --profile) and an access point it is pinned to.
    # A helper below version 18 names none of them.
    auth: str | None = None
    profile: str | None = None
    bssid: str | None = None
    stage: str | None = None
    result: str | None = None
    code: str | None = None
    age: float | None = None
    asked_every: float | None = None
    asked_ago: float | None = None
    interval: float | None = None
    idle: bool = False
    expires_in: float | None = None
    sensor_id: str | None = None


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
    # The newest version the probe's package manager would install, from its
    # local cache. Older helpers do not report it.
    package_available: str | None = None
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
    # The return channel as the helper reports it (ADR 0013): the expiry date
    # of the probe's client certificate and the state of its service. None
    # where the probe has none, or a helper too old to say.
    channel_cert: str | None = None
    channel_service: str | None = None
    # When that certificate was issued, as ISO 8601 UTC. From helper version
    # 13 on, the one that also sends heartbeats: only with both can a probe
    # that never delivered be told apart from one that has nothing to say.
    channel_cert_issued: str | None = None

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
            "package_available": self.package_available,
            "hostname": self.hostname,
            "ca_sha256": self.ca_sha256,
            "config_path": self.config_path,
            "probe_id": self.probe_id,
            "probe_name": self.probe_name,
            "has_access_key": self.has_access_key,
            "helper_version": self.helper_version,
            "helper_sha256": self.helper_sha256,
            "platform": self.platform,
            "channel_cert": self.channel_cert,
            "channel_service": self.channel_service,
            "channel_cert_issued": self.channel_cert_issued,
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
            package_available=document.get("package_available"),
            hostname=document.get("hostname"),
            ca_sha256=document.get("ca_sha256"),
            config_path=document.get("config_path"),
            probe_id=document.get("probe_id"),
            probe_name=document.get("probe_name"),
            has_access_key=bool(document.get("has_access_key")),
            helper_version=document.get("helper_version"),
            helper_sha256=document.get("helper_sha256"),
            platform=document.get("platform"),
            channel_cert=document.get("channel_cert"),
            channel_service=document.get("channel_service"),
            channel_cert_issued=document.get("channel_cert_issued"),
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
        package_available=normalise_optional(response.value("package_available")),
        hostname=normalise_optional(response.value("hostname")),
        ca_sha256=normalise_optional(response.value("ca_sha256")),
        config_path=normalise_optional(response.value("config")),
        probe_id=normalise_optional(response.value("id")),
        probe_name=normalise_optional(response.value("name")),
        has_access_key=normalise_optional(response.value("access_key")) is not None,
        helper_version=_helper_version(response.value("helper_version")),
        helper_sha256=normalise_optional(response.value("helper_sha256")),
        platform=normalise_optional(response.value("platform")),
        channel_cert=normalise_optional(response.value("channel_cert")),
        channel_service=normalise_optional(response.value("channel_service")),
        channel_cert_issued=normalise_optional(response.value("channel_cert_issued")),
    )


def _order(char: str) -> int:
    # dpkg's character order: "~" sorts before everything, even the end of
    # the string, letters before other characters.
    if char == "~":
        return -1
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _compare_part(left: str, right: str) -> int:
    """dpkg's verrevcmp for one upstream version or revision."""
    i = j = 0
    while i < len(left) or j < len(right):
        while (i < len(left) and not left[i].isdigit()) or (
            j < len(right) and not right[j].isdigit()
        ):
            a = _order(left[i]) if i < len(left) and not left[i].isdigit() else 0
            b = _order(right[j]) if j < len(right) and not right[j].isdigit() else 0
            if a != b:
                return -1 if a < b else 1
            i += 1
            j += 1
        start_i, start_j = i, j
        while i < len(left) and left[i].isdigit():
            i += 1
        while j < len(right) and right[j].isdigit():
            j += 1
        a_num = int(left[start_i:i] or "0")
        b_num = int(right[start_j:j] or "0")
        if a_num != b_num:
            return -1 if a_num < b_num else 1
    return 0


def _split_version(version: str) -> tuple[int, str, str]:
    epoch, _, rest = version.rpartition(":") if ":" in version else ("0", "", version)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return (int(epoch) if epoch.isdigit() else 0), upstream, revision


def compare_package_versions(left: str, right: str) -> int:
    """-1, 0 or 1, the way dpkg orders Debian versions.

    RPM versions as reported here are plain dotted numbers, which this orders
    the same way rpm does.
    """
    left_epoch, left_upstream, left_revision = _split_version(left)
    right_epoch, right_upstream, right_revision = _split_version(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    return _compare_part(left_upstream, right_upstream) or _compare_part(
        left_revision, right_revision
    )


def newer_package_version(
    installed: str | None, known: Iterable[str | None]
) -> str | None:
    """The newest of ``known`` if it is newer than ``installed``, else None.

    ``known`` is what the probes of the same platform report, installed and
    available alike. A probe whose package cache is weeks old still learns
    about a release once another probe like it has seen it.
    """
    if installed is None:
        return None
    # Debian versions carry a revision ("3.11.0-1"), RPM versions as reported
    # here do not ("3.11.0"). Only versions of the same kind are compared.
    has_revision = "-" in installed
    newest: str | None = None
    for version in known:
        if version is None or ("-" in version) != has_revision:
            continue
        if compare_package_versions(version, installed) <= 0:
            continue
        if newest is None or compare_package_versions(version, newest) > 0:
            newest = version
    return newest


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


def parse_sensor_jobs(response: HelperResponse) -> tuple[SensorJob, ...]:
    def number(value: str | None) -> float | None:
        try:
            return float(value) if value not in (None, "", "-") else None
        except ValueError:
            return None

    def text(value: str | None) -> str | None:
        return None if value in (None, "", "-") else value

    jobs = []
    for record in response.records:
        try:
            ssid = bytes.fromhex(record.get("ssid_hex") or "").decode(
                "utf-8", "replace"
            )
        except ValueError:
            ssid = ""
        jobs.append(
            SensorJob(
                label=record["name"],
                interface=text(record.get("interface")),
                ssid=ssid or None,
                auth=text(record.get("auth")),
                profile=text(record.get("profile")),
                bssid=text(record.get("bssid")),
                stage=text(record.get("stage")),
                result=text(record.get("result")),
                code=text(record.get("code")),
                age=number(record.get("age")),
                asked_every=number(record.get("asked_every")),
                asked_ago=number(record.get("asked_ago")),
                interval=number(record.get("interval")),
                idle=record.get("idle") == "yes",
                expires_in=number(record.get("expires_in")),
                sensor_id=text(record.get("sensor_id")),
            )
        )
    return tuple(jobs)


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
    # A newer probe package than the installed one, as far as the fleet knows,
    # or None when the probe is current.
    package_update: str | None = None
    # The operator's tick that the access key was entered in PRTG and the
    # probe approved there. Not part of the status: a probe without it is
    # healthy here and invisible over there, which is exactly why it needs
    # its own signal.
    prtg_registered: bool = False
    # The last management answer came over the overlay, recently enough to
    # count as current. The list marks these so a probe whose tunnel quietly
    # stopped carrying management stands out from the ones it still does.
    managed_over_tunnel: bool = False
    # The return channel has been quiet for longer than the silence
    # threshold. Carried in the row so the alert derivation and the automatic
    # repair read it from the same summary the list shows.
    channel_silent: bool = False
    # The probe reported a client certificate that has expired. The channel
    # cannot deliver with it, so the repair need not wait for the silence.
    channel_certificate_expired: bool = False


@dataclass(frozen=True, slots=True)
class ManagementRoute:
    """The address a probe last answered management on, and when."""

    host: str
    over_overlay: bool
    observed_at: datetime
    # Only meaningful when read back: a route just observed is current.
    stale: bool = False


@dataclass(frozen=True, slots=True)
class ReturnChannelState:
    """A probe's return channel: what it last delivered and what it runs.

    The delivery half comes from the probe's batches, the certificate and
    service half from its last probe-info; either may be missing.
    """

    last_contact_at: datetime | None = None
    buffer_bytes: int | None = None
    buffer_limit: int | None = None
    gap_count: int = 0
    last_gap_at: datetime | None = None
    observed_at: datetime | None = None
    certificate_expires: str | None = None
    certificate_issued_at: datetime | None = None
    certificate_expired: bool = False
    service: str | None = None
    silent: bool = False


NatsRoute = Literal["tunnel", "direct"]


def nats_route(
    *, on_overlay: bool, overlay_mode: str, last_state: str | None
) -> NatsRoute | None:
    """Where the probe's NATS traffic goes, from the overlay peer's last state.

    ``down`` counts as direct: with the interface gone there is no tunnel
    route to take. A tunnel without a handshake, or one never asked, says
    nothing either way, so it is not reported as either.
    """
    if not on_overlay or overlay_mode == "off":
        return "direct"
    if last_state == "tunnel":
        return "tunnel"
    if last_state in ("direct", "down", "off"):
        return "direct"
    return None
