"""Probe payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.api.schemas.common import ApiModel
from app.domain.enums import (
    CaState,
    DeviationKind,
    DeviationSeverity,
    NatsConnectionState,
    ProbeStatus,
    SensorInstallationStatus,
    ServiceState,
)
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN


class ProbeSummaryOut(ApiModel):
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
    # True when the cached state is older than the configured window. The
    # interface dims the row rather than pretending the value is fresh.
    stale: bool
    running_job_id: str | None = None
    error_code: str | None = None
    helper_version: int | None = None
    helper_outdated: bool = False


class SensorStateOut(ApiModel):
    name: str
    status: SensorInstallationStatus
    desired_version: str | None
    installed_version: str | None
    installed_sha256: str | None
    expected_sha256: str | None
    interfaces: list[str] = Field(default_factory=list)
    helper_state: str | None = None
    # Which half drifted is the first question a drifted sensor raises, so
    # the helper's digests travel alongside the script's.
    installed_helper_sha256: str | None = None
    expected_helper_sha256: str | None = None


class DeviationOut(ApiModel):
    kind: DeviationKind
    severity: DeviationSeverity
    object_type: str
    object_ref: str
    expected: str | None = None
    actual: str | None = None
    remediation: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ProbeInventoryOut(ApiModel):
    ssh_host: str
    ssh_port: int
    probe_id: str | None
    probe_name: str | None
    # The PRTG access key is a secret; only its presence is reported here.
    access_key_present: bool
    pending_transaction: str | None
    assigned_sensors: list[str] = Field(default_factory=list)
    known_iperf_endpoints: list[str] = Field(default_factory=list)


class AccessKeyOut(ApiModel):
    """The revealed access key, next to the probe it belongs to.

    The account name travels with the value so the interface can name what it
    is showing without trusting the route parameter it sent.
    """

    nats_username: str
    access_key: str


class ObservedStateOut(ApiModel):
    observed_at: datetime
    reachable: bool
    service: ServiceState
    package_version: str | None
    hostname: str | None
    ca_sha256: str | None
    config_path: str | None
    probe_id: str | None
    probe_name: str | None
    helper_version: int | None = None
    helper_sha256: str | None = None
    helper_outdated: bool = False
    error_code: str | None = None
    error_details: str | None = None


class ProbeDetailOut(ApiModel):
    summary: ProbeSummaryOut
    inventory: ProbeInventoryOut
    observed: ObservedStateOut | None
    sensors: list[SensorStateOut]
    deviations: list[DeviationOut]
    notes: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class ProbeUpdateIn(ApiModel):
    display_name: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)
    labels: dict[str, str] | None = None


class DesiredSensorIn(ApiModel):
    name: str
    version: str | None = None
    profiles: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not NATS_USERNAME_PATTERN.match(value):
            raise ValueError("invalid sensor name")
        return value


class DesiredStateIn(ApiModel):
    sensors: list[DesiredSensorIn] = Field(default_factory=list)
    probe_name: str | None = None
    ca_required: bool = True


class DesiredStateOut(ApiModel):
    version: int
    sensors: list[DesiredSensorIn]
    probe_name: str | None
    ca_required: bool
    updated_at: datetime | None = None
    author_name: str | None = None


class PlannedActionOut(ApiModel):
    kind: str
    target: str
    description_key: str
    params: dict[str, str] = Field(default_factory=dict)
    restarts_service: bool = False
    risk_key: str | None = None


class ReconciliationPlanOut(ApiModel):
    """The preview shown before "fix deviations" runs anything."""

    probe_username: str
    deviations: list[DeviationOut]
    actions: list[PlannedActionOut]
    restarts_service: bool
    is_empty: bool
