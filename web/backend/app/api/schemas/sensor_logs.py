"""Sensor log records, as a probe sends them and as the interface reads them."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.api.schemas.common import ApiModel
from app.services.sensor_logs import SENSOR_NAME_PATTERN

RecordResult = Literal["ok", "error", "gap"]

# Generous for one sensor run with its raw lines, small enough that a probe
# catching up after an outage sends several batches rather than one huge one.
MAX_BATCH_RECORDS = 500


class SensorLogRecordIn(ApiModel):
    """One record from a probe's buffer.

    Anything the probe adds beyond the envelope is dropped - a ``probe`` field
    above all: who sent a record is what the transport established, never
    what the record claims.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID
    sensor: str = Field(pattern=SENSOR_NAME_PATTERN.pattern)
    # A label the probe gives the sensor's job. Not an ID of ours.
    job: str | None = Field(default=None, max_length=64)
    time: datetime
    result: RecordResult
    code: str | None = Field(default=None, max_length=128)
    summary: str = Field(default="", max_length=2000)
    body: dict[str, Any] = Field(default_factory=dict)


class ProbeBufferIn(ApiModel):
    """How full the probe's buffer was when it sent the batch."""

    bytes: int = Field(ge=0)
    limit: int = Field(ge=0)


class SensorLogBatchIn(ApiModel):
    records: list[SensorLogRecordIn] = Field(max_length=MAX_BATCH_RECORDS)
    buffer: ProbeBufferIn | None = None


class SensorLogBatchOut(ApiModel):
    """What the probe may drop from its buffer: both lists are acknowledged."""

    stored: list[str]
    duplicates: list[str]


class SensorLogSummaryOut(ApiModel):
    id: str
    probe: str
    sensor: str
    job: str | None = None
    time: datetime
    result: RecordResult
    code: str | None = None
    summary: str = ""
    # From the body, because filtering and grouping by network is what the
    # list is for and the body itself stays out of it.
    ssid: str | None = None


class SensorLogRecordOut(SensorLogSummaryOut):
    body: dict[str, Any] = Field(default_factory=dict)


class SensorLogPageOut(ApiModel):
    items: list[SensorLogSummaryOut]
    total: int
    limit: int
    offset: int
