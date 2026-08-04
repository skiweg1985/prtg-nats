"""System, sensor, job and audit payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.domain.enums import (
    AlertSeverity,
    AuditResult,
    CertificateKind,
    CertificateStatus,
    JobStatus,
    JobStepStatus,
    LogLevel,
)

# --- System -----------------------------------------------------------------


class SiteSettingsOut(ApiModel):
    nats_fqdn: str | None
    nats_port: int
    nats_host_ip: str | None
    ca_http_port: int
    ca_organization: str
    prtg_core_ip: str | None
    nats_endpoint: str | None
    is_configured: bool


class ContainerStateOut(ApiModel):
    name: str
    exists: bool
    running: bool
    status: str | None = None
    health: str | None = None
    image: str | None = None
    restart_count: int = 0


class JetStreamOut(ApiModel):
    enabled: bool
    streams: int = 0
    consumers: int = 0
    messages: int = 0
    bytes_used: int = 0
    store_used: int = 0
    store_limit: int = 0
    store_usage_ratio: float | None = None


class NatsStateOut(ApiModel):
    available: bool
    healthy: bool
    server_name: str | None = None
    version: str | None = None
    uptime: str | None = None
    connections: int = 0
    slow_consumers: int = 0
    jetstream: JetStreamOut | None = None
    connected_user_count: int = 0
    error_details: str | None = None


class BackupFileOut(ApiModel):
    """An archive sitting in the runtime volume, and how to get it out."""

    name: str
    kind: str  # "runtime" | "jetstream"
    size_bytes: int
    created_at: datetime
    sha256: str | None = None
    download_url: str


class CertificateOut(ApiModel):
    kind: CertificateKind
    path: str
    status: CertificateStatus
    subject: str | None = None
    issuer: str | None = None
    not_after: datetime | None = None
    days_remaining: int | None = None
    sha256: str | None = None
    subject_alt_names: list[str] = Field(default_factory=list)
    key_matches: bool | None = None


class CapabilitiesOut(ApiModel):
    """What this installation can actually do.

    The interface hides actions it cannot perform rather than offering a button
    that fails. Docker is the interesting one: without the socket, server
    lifecycle control is simply absent and everything else still works.
    """

    docker: bool
    runtime_state: str
    dev_auth: bool


class SystemStatusOut(ApiModel):
    site: SiteSettingsOut
    nats: NatsStateOut
    containers: list[ContainerStateOut]
    certificates: list[CertificateOut]
    capabilities: CapabilitiesOut
    runtime_missing: list[str] = Field(default_factory=list)


class AlertOut(ApiModel):
    id: str
    kind: str
    severity: AlertSeverity
    object_type: str
    object_ref: str
    object_label: str
    params: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None = None


class DashboardOut(ApiModel):
    """Everything the landing page needs, in one request.

    A dashboard assembled from eight parallel calls is eight chances to show a
    half-drawn page; this is one call that either renders or explains itself.
    """

    system: SystemStatusOut
    probe_total: int
    probe_healthy: int
    probe_degraded: int
    probe_unreachable: int
    probes_with_deviations: int
    failed_jobs_24h: int
    running_jobs: int
    expiring_certificates: list[CertificateOut]
    alerts: list[AlertOut]
    recent_jobs: list[JobSummaryOut]
    recent_audit: list[AuditEventOut]


# --- Sensors ----------------------------------------------------------------


class SensorFileOut(ApiModel):
    slot: str
    relative_path: str
    size_bytes: int
    sha256: str


class SensorSummaryOut(ApiModel):
    name: str
    version: str
    description: str
    needs_interface: bool
    requires_privileged_helper: bool
    iperf_kind: str | None = None
    has_parameter_schema: bool = False
    # Whether the sensor takes settings, credentials or files, and can
    # therefore be configured into variants at all.
    supports_profiles: bool = False
    # How many enrolled probes currently run it, from cached observed state.
    installed_on: int = 0
    outdated_on: int = 0


class ParameterFieldOut(ApiModel):
    """One option of the sensor, as the PRTG parameter line takes it."""

    name: str
    type: str
    required: bool = False
    default: Any = None
    choices: list[str] = Field(default_factory=list)
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str | None = None
    repeatable: bool = False
    # "prtg" means PRTG substitutes the value; the interface then shows the
    # placeholder instead of asking for it.
    source: str = "manual"
    prtg_placeholder: str | None = None


class ProfileFieldOut(ApiModel):
    """One ``KEY=VALUE`` line of a variant - a setting or a credential."""

    name: str
    type: str
    required: bool = False
    default: Any = None
    choices: list[str] = Field(default_factory=list)
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    sensitive: bool = False
    maps_to: str | None = None


class FileFieldOut(ApiModel):
    """A certificate or key that travels with a variant."""

    name: str
    kind: str = "file"
    secret: bool = False
    required: bool = False
    max_bytes: int
    extension: str
    description: str = ""
    label_key: str | None = None
    description_key: str | None = None
    group: str | None = None
    maps_to: str | None = None


class SensorSchemaOut(ApiModel):
    """What the sensor declares in ``parameters.json``.

    Typed rather than passed through as raw JSON: the interface renders four
    different things from it, and guessing the shape at the other end is how
    a renamed key becomes a blank form instead of an error.
    """

    parameters: list[ParameterFieldOut] = Field(default_factory=list)
    settings: list[ProfileFieldOut] = Field(default_factory=list)
    credentials: list[ProfileFieldOut] = Field(default_factory=list)
    files: list[FileFieldOut] = Field(default_factory=list)
    supports_profiles: bool = False
    # Every required parameter with its placeholders filled in - the line an
    # operator would otherwise copy out of the README by hand.
    default_parameter_line: str = ""


class SensorDetailOut(ApiModel):
    name: str
    version: str
    description: str
    needs_interface: bool
    requires_privileged_helper: bool
    iperf_kind: str | None
    files: list[SensorFileOut]
    parameter_schema: SensorSchemaOut | None = None
    readme: str | None = None
    profile_template: str | None = None
    probes: list[str] = Field(default_factory=list)


class RenderParametersIn(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class RenderParametersOut(ApiModel):
    """The exact string to paste into the PRTG sensor's parameter field."""

    parameters: str


# --- Sensor variants --------------------------------------------------------


class SensorProfileFileOut(ApiModel):
    """An uploaded certificate or key, described but never handed back.

    Not even a public certificate is returned: the fingerprint answers "is this
    still the one I uploaded" without the endpoint ever becoming a way to read
    files out of the runtime directory.
    """

    key: str
    filename: str
    size_bytes: int
    sha256: str
    # Where it sits on the probe, which is also what stands in the profile.
    probe_path: str


class SensorProfileOut(ApiModel):
    sensor: str
    name: str
    updated_at: datetime | None = None
    probes: list[str] = Field(default_factory=list)
    files: list[SensorProfileFileOut] = Field(default_factory=list)
    # The line that selects this variant in PRTG.
    parameter_line: str = ""


class SensorProfileDetailOut(SensorProfileOut):
    """One variant, as far as it may be shown.

    ``values`` holds the settings only. A credential is never read back - what
    is returned is its name in ``secrets_set``, so the form can say "stored,
    leave empty to keep" instead of pretending the field is empty.
    """

    values: dict[str, str] = Field(default_factory=dict)
    secrets_set: list[str] = Field(default_factory=list)


class SensorProfileIn(ApiModel):
    values: dict[str, str] = Field(default_factory=dict)
    # Which probes are meant to hold it. An empty list stores the variant
    # without deploying it, which is how one is prepared before the site exists.
    probes: list[str] = Field(default_factory=list)


class SensorProfileFileIn(ApiModel):
    """A certificate or key on its way in.

    Base64 because that is the encoding it travels in from here to the probe;
    one representation for the whole path beats a format change in the middle.
    """

    content_base64: str = Field(min_length=1)


# --- Deployments ------------------------------------------------------------


class DeploymentCreateIn(ApiModel):
    sensor: str
    probe_ids: list[str] = Field(min_length=1)
    dry_run: bool = False


class DeploymentTargetOut(ApiModel):
    probe_id: str
    probe_label: str
    status: JobStatus
    previous_version: str | None = None
    error_code: str | None = None
    error_details: str | None = None
    finished_at: datetime | None = None


class DeploymentOut(ApiModel):
    id: str
    sensor_name: str
    sensor_version: str
    status: JobStatus
    job_id: str | None
    dry_run: bool
    requested_by_name: str | None
    created_at: datetime
    targets: list[DeploymentTargetOut]


# --- Jobs -------------------------------------------------------------------


class JobStepOut(ApiModel):
    name: str
    position: int
    status: JobStepStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    target_label: str | None = None


class JobEventOut(ApiModel):
    sequence: int
    ts: datetime
    level: LogLevel
    step: str | None
    target: str | None
    # Translated in the browser; `raw` never is.
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    raw: str | None = None


class JobSummaryOut(ApiModel):
    id: str
    type: str
    status: JobStatus
    target_type: str | None
    target_id: str | None
    target_label: str | None
    progress: int
    current_step: str | None
    requested_by_name: str | None
    trigger: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    blocked_reason_key: str | None = None
    blocked_by_job_id: str | None = None
    error_code: str | None = None


class JobDetailOut(JobSummaryOut):
    steps: list[JobStepOut]
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_params: dict[str, Any] | None = None
    error_details: str | None = None
    retry_of_job_id: str | None = None


# --- Audit ------------------------------------------------------------------


class AuditEventOut(ApiModel):
    id: str
    ts: datetime
    actor_name: str
    source_ip: str | None
    action: str
    object_type: str
    object_id: str | None
    object_label: str | None
    result: AuditResult
    error_code: str | None = None
    job_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    comment: str | None = None


# --- iperf ------------------------------------------------------------------


class IperfEndpointOut(ApiModel):
    name: str
    host: str
    port: int
    username: str
    kind: str
    updated_at: datetime | None
    has_public_key: bool
    # Whether this platform set the host up and can still reach it. False for
    # one somebody else operates: its password is not ours to rotate, and
    # removing it here takes nothing off that host.
    managed: bool = True
    # Which probes hold credentials for it, from runtime/probes/USER.iperf.
    deployed_to: list[str] = Field(default_factory=list)


DashboardOut.model_rebuild()
