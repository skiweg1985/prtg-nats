"""Request and response shapes for the availability monitoring."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.api.schemas.common import ApiModel
from app.domain.enums import WatchCheckMethod, WatchState

# A label key or value that is anything but plain text turns into a filter
# nobody can type and a URL nobody can read. Bounded here rather than at the
# column, so the interface gets a 422 with a reason instead of a 500.
MAX_LABELS = 12
MAX_LABEL_LENGTH = 64


class WatchDeviceIn(ApiModel):
    display_name: str = Field(min_length=1, max_length=128)
    address: str = Field(min_length=1, max_length=255)
    probe_id: str = Field(min_length=1, max_length=26)
    method: WatchCheckMethod = WatchCheckMethod.ICMP
    port: int | None = Field(default=None, ge=1, le=65535)
    labels: dict[str, str] = Field(default_factory=dict)
    # Three misses at a one-minute interval is three minutes before a device
    # counts as down. Low enough for support to notice, high enough that a
    # card terminal dropping the odd packet stays out of the history.
    failure_threshold: int = Field(default=3, ge=1, le=10)
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("labels")
    @classmethod
    def _bounded_labels(cls, labels: dict[str, str]) -> dict[str, str]:
        if len(labels) > MAX_LABELS:
            raise ValueError(f"at most {MAX_LABELS} labels")
        for key, value in labels.items():
            if not key or len(key) > MAX_LABEL_LENGTH:
                raise ValueError(f"label keys are 1 to {MAX_LABEL_LENGTH} characters")
            if len(value) > MAX_LABEL_LENGTH:
                raise ValueError(f"label values are at most {MAX_LABEL_LENGTH}")
        return labels


class WatchDeviceUpdateIn(ApiModel):
    """Every field optional; absent means unchanged."""

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    address: str | None = Field(default=None, min_length=1, max_length=255)
    probe_id: str | None = Field(default=None, min_length=1, max_length=26)
    method: WatchCheckMethod | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    labels: dict[str, str] | None = None
    failure_threshold: int | None = Field(default=None, ge=1, le=10)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class WatchDeviceOut(ApiModel):
    id: str
    display_name: str
    address: str
    probe_id: str
    probe_name: str
    method: WatchCheckMethod
    port: int | None
    labels: dict[str, str]
    enabled: bool
    failure_threshold: int
    notes: str | None

    # The last measurement. Null throughout when the device has never been
    # measured - a device added a minute ago is not a device that is down.
    state: WatchState
    observed_at: datetime | None
    rtt_ms: float | None
    error: str | None
    # True when the last measurement is older than the platform is willing to
    # call current. The interface greys the row rather than showing a state
    # that stopped being true.
    stale: bool


class WatchAvailabilityOut(ApiModel):
    device_id: str
    since: datetime
    until: datetime
    up_seconds: float
    down_seconds: float
    unknown_seconds: float
    outages: int
    longest_outage_seconds: float
    # Null when nothing was measured in the window. Not zero - see the
    # AvailabilitySummary it comes from.
    ratio: float | None


class WatchOutageOut(ApiModel):
    device_id: str
    device_name: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    reason: str | None


class WatchOverviewOut(ApiModel):
    """What the dashboard reads in one request."""

    devices: list[WatchDeviceOut]
    up: int
    down: int
    unknown: int
    # Every label key with the values in use, for the filter menu.
    labels: dict[str, list[str]]
    # Whether the platform is connected to NATS at all. A dashboard full of
    # unknown devices has two possible causes, and this is the one the
    # operator can do something about.
    receiving: bool
