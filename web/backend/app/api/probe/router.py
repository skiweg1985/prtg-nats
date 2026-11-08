"""Version 1 of the return channel: records in, acknowledgements out."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps.common import BroadcasterDep, SensorLogStoreDep
from app.api.probe.deps import ProbeIdentityDep
from app.api.schemas.sensor_logs import SensorLogBatchIn, SensorLogBatchOut
from app.services.sensor_logs import format_time, ingest

probe_router = APIRouter(prefix="/probe/v1", tags=["probe channel"])


@probe_router.post("/records", response_model=SensorLogBatchOut)
async def submit_records(
    batch: SensorLogBatchIn,
    probe: ProbeIdentityDep,
    store: SensorLogStoreDep,
    broadcaster: BroadcasterDep,
) -> SensorLogBatchOut:
    """Store a batch of sensor log records from one probe.

    Idempotent on the record ID: a batch sent again after a lost answer comes
    back with its IDs under ``duplicates``, and the probe drops both lists
    from its buffer.
    """
    records = [
        {
            "id": str(item.id),
            "sensor": item.sensor,
            "job": item.job,
            "time": format_time(item.time),
            "result": item.result,
            "code": item.code,
            "summary": item.summary,
            "body": item.body,
        }
        for item in batch.records
    ]
    outcome = await ingest(store, broadcaster, probe, records)
    return SensorLogBatchOut(
        stored=[str(entry["id"]) for entry in outcome.stored],
        duplicates=outcome.duplicates,
    )
