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
    # How many enrolled probes currently run it, from cached observed state.
    installed_on: int = 0
    outdated_on: int = 0


class SensorDetailOut(ApiModel):
    name: str
    version: str
    description: str
    needs_interface: bool
    requires_privileged_helper: bool
    iperf_kind: str | None
    files: list[SensorFileOut]
    parameter_schema: dict[str, Any] | None = None
    readme: str | None = None
    profile_template: str | None = None
    probes: list[str] = Field(default_factory=list)


class RenderParametersIn(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class RenderParametersOut(ApiModel):
    """The exact string to paste into the PRTG sensor's parameter field."""

    parameters: str


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
    # Which probes hold credentials for it, from runtime/probes/USER.iperf.
    deployed_to: list[str] = Field(default_factory=list)


DashboardOut.model_rebuild()
