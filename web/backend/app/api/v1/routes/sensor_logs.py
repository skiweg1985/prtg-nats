"""Reading what probes reported over their return channel, and following it live."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    BroadcasterDep,
    DbSession,
    PrincipalDep,
    SensorLogStoreDep,
    require_permission,
)
from app.api.schemas.sensor_logs import (
    HiddenStreamOut,
    HideStreamIn,
    RecordResult,
    SensorLogBucketOut,
    SensorLogNeighboursOut,
    SensorLogPageOut,
    SensorLogRecordOut,
    SensorLogSegmentOut,
    SensorLogStreamOut,
    SensorLogSummaryOut,
    SensorLogTimelineOut,
    SensorLogTimelineStreamOut,
)
from app.api.sse import KEEPALIVE, event_stream, follow, sse
from app.core.errors import NotFoundError
from app.core.permissions import Permission
from app.persistence.models import ProbeRecord
from app.services import hidden_streams
from app.services.events import StreamEvent, sensor_log_topic
from app.services.sensor_logs import SensorLogFilter

router = APIRouter(prefix="/sensor-logs", tags=["sensor logs"])

# What a page shows when nobody narrowed the time range: the last day, which
# is what an operator looking into a red sensor wants first.
DEFAULT_WINDOW = timedelta(hours=24)

ReadGuard = Annotated[object, Depends(require_permission(Permission.SENSOR_LOG_READ))]


@router.get("", response_model=SensorLogPageOut)
async def list_sensor_logs(
    store: SensorLogStoreDep,
    _: ReadGuard,
    probe: Annotated[str | None, Query()] = None,
    sensor: Annotated[str | None, Query()] = None,
    ssid: Annotated[str | None, Query()] = None,
    variant: Annotated[str | None, Query()] = None,
    result: Annotated[RecordResult | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SensorLogPageOut:
    """Records matching every given filter, newest first.

    ``q`` searches the summary, the error code and the raw output in the body,
    case-insensitively. Without ``since`` the last 24 hours are searched.
    """
    end = _aware(until) if until else datetime.now(UTC)
    start = _aware(since) if since else end - DEFAULT_WINDOW
    filters = SensorLogFilter(
        since=start,
        until=end,
        probe=probe or None,
        sensor=sensor or None,
        ssid=ssid or None,
        variant=variant or None,
        result=result,
        text=q or None,
    )
    page = await asyncio.to_thread(store.query, filters, limit=limit, offset=offset)
    return SensorLogPageOut(
        items=[SensorLogSummaryOut.model_validate(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/streams", response_model=list[SensorLogStreamOut])
async def sensor_log_streams(
    store: SensorLogStoreDep,
    _: ReadGuard,
    probe: Annotated[str, Query(min_length=1)],
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    segments: Annotated[int, Query(ge=1, le=50)] = 8,
) -> list[SensorLogStreamOut]:
    """One probe's runs per sensor and network, runs in a row folded together.

    What the probe page shows. The full list stays at ``GET /sensor-logs``.
    Without ``since`` the last 24 hours are folded.
    """
    end = _aware(until) if until else datetime.now(UTC)
    start = _aware(since) if since else end - DEFAULT_WINDOW
    streams = await asyncio.to_thread(
        store.streams,
        SensorLogFilter(since=start, until=end, probe=probe),
        segments=segments,
    )
    return [
        SensorLogStreamOut(
            sensor=stream.sensor,
            ssid=stream.ssid,
            total=stream.total,
            results=stream.results,
            segments=[
                SensorLogSegmentOut(
                    result=segment.result,
                    code=segment.code,
                    count=segment.count,
                    first=SensorLogSummaryOut.model_validate(segment.first),
                    last=SensorLogSummaryOut.model_validate(segment.last),
                )
                for segment in stream.segments
            ],
            more_segments=stream.more_segments,
        )
        for stream in streams
    ]


# Bucket sizes the timeline accepts: from five minutes to a day, and never
# more slices than a bar can draw.
MIN_BUCKET_SECONDS = 300
MAX_BUCKET_SECONDS = 86400
MAX_BUCKETS = 1000


@router.get("/timeline", response_model=SensorLogTimelineOut)
async def sensor_log_timeline(
    store: SensorLogStoreDep,
    db: DbSession,
    _: ReadGuard,
    probe: Annotated[str, Query(min_length=1)],
    sensor: Annotated[str | None, Query(min_length=1)] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    bucket: Annotated[int, Query(ge=MIN_BUCKET_SECONDS, le=MAX_BUCKET_SECONDS)] = 900,
) -> SensorLogTimelineOut:
    """A probe's runs per sensor and network, sliced into buckets.

    What the sensor page on a probe draws its timeline from, narrowed by
    ``sensor``; without it every sensor, for the probe page's overview.
    Without ``since`` the last 24 hours, in buckets of ``bucket`` seconds.
    A row somebody hid stays out until a run newer than the moment it was
    hidden arrives.
    """
    end = _aware(until) if until else datetime.now(UTC)
    start = _aware(since) if since else end - DEFAULT_WINDOW
    if (end - start).total_seconds() / bucket > MAX_BUCKETS:
        start = end - timedelta(seconds=bucket * MAX_BUCKETS)
    first, streams = await asyncio.to_thread(
        store.timeline,
        SensorLogFilter(since=start, until=end, probe=probe, sensor=sensor),
        bucket=timedelta(seconds=bucket),
    )
    streams = hidden_streams.visible(
        streams, await hidden_streams.hidden_since(db, probe, sensor)
    )
    return SensorLogTimelineOut(
        probe=probe,
        sensor=sensor,
        start=first,
        until=end,
        bucket_seconds=bucket,
        retained_since=store.retained_since(),
        streams=[
            SensorLogTimelineStreamOut(
                sensor=stream.sensor,
                stream=stream.key,
                ssid=stream.ssid,
                variant=stream.variant,
                ok=stream.ok,
                error=stream.error,
                success_rate=stream.success_rate,
                runs_per_hour=stream.runs_per_hour,
                last_failure=SensorLogSummaryOut.model_validate(stream.last_failure)
                if stream.last_failure
                else None,
                last_run=stream.last_run,
                ended=stream.ended,
                last_job=stream.last_job,
                buckets=[
                    SensorLogBucketOut(
                        start=b.start,
                        ok=b.ok,
                        error=b.error,
                        state=b.state,
                        code=b.code,
                    )
                    for b in stream.buckets
                ],
            )
            for stream in streams
        ],
    )


@router.get("/timeline/run", response_model=SensorLogSummaryOut)
async def sensor_log_run_at(
    store: SensorLogStoreDep,
    _: ReadGuard,
    probe: Annotated[str, Query(min_length=1)],
    sensor: Annotated[str, Query(min_length=1)],
    since: Annotated[datetime, Query()],
    until: Annotated[datetime, Query()],
    stream: Annotated[str, Query(max_length=255)] = "",
) -> SensorLogSummaryOut:
    """The run a click on one slice of a timeline row opens.

    ``stream`` is the row's key from ``GET /sensor-logs/timeline``. The first
    failure in the slice if it has one, else its first run; a slice without
    a run of its own opens the run before it, whose state it is drawn in.
    """
    found = await asyncio.to_thread(
        store.run_at, probe, sensor, stream, _aware(since), _aware(until)
    )
    if found is None:
        raise NotFoundError.of("sensor_log", f"{probe}/{sensor}/{stream}")
    return SensorLogSummaryOut.model_validate(found)


@router.post("/hidden-streams", response_model=HiddenStreamOut)
async def hide_sensor_log_stream(
    payload: HideStreamIn,
    db: DbSession,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> HiddenStreamOut:
    """Take a row off a sensor's timeline until its next run.

    The records stay in the sensor log. Changes what every user sees on the
    page, so it takes the same permission as changing the sensor's setup.
    """
    record = (
        await db.execute(
            select(ProbeRecord).where(ProbeRecord.nats_username == payload.probe)
        )
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError.of("probe", payload.probe)
    row, previous = await hidden_streams.hide(
        db,
        record,
        payload.sensor,
        payload.stream,
        now=datetime.now(UTC),
        by=principal.username,
    )
    audit.record(
        action="sensor_log.hide_stream",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        before={"hidden_at": previous.isoformat()} if previous else None,
        after={
            "sensor": payload.sensor,
            "stream": payload.stream,
            "hidden_at": row.hidden_at.isoformat(),
        },
    )
    return HiddenStreamOut(
        probe=record.nats_username,
        sensor=row.sensor,
        stream=row.stream,
        hidden_at=row.hidden_at,
    )


@router.get("/events")
async def sensor_log_events(
    request: Request,
    db: DbSession,
    broadcaster: BroadcasterDep,
    _: ReadGuard,
    probe: Annotated[str | None, Query()] = None,
    sensor: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Server-sent events, one ``sensor_log.record`` per newly stored record.

    Only what arrives while the stream is open: a page loads the list first
    and adds what this announces. Narrowed to one probe or sensor on request.
    """
    topic = sensor_log_topic()
    queue = await broadcaster.register(topic)
    try:
        # Authentication touched the session row; the stream must not hold
        # SQLite's write transaction for as long as the page stays open. Same
        # reasoning as the job stream in routes/jobs.py.
        await db.commit()
        await db.close()
    except BaseException:
        await broadcaster.unregister(topic, queue)
        raise

    def wanted(event: StreamEvent) -> bool:
        if probe and event.payload.get("probe") != probe:
            return False
        return not (sensor and event.payload.get("sensor") != sensor)

    async def stream() -> AsyncIterator[bytes]:
        try:
            async with contextlib.aclosing(follow(request, queue)) as updates:
                async for update in updates:
                    if update is None:
                        yield KEEPALIVE
                    elif wanted(update):
                        yield sse(update.kind, update.payload)
        finally:
            await broadcaster.unregister(topic, queue)

    return event_stream(stream())


@router.get("/{record_id}", response_model=SensorLogRecordOut)
async def get_sensor_log(
    record_id: str,
    store: SensorLogStoreDep,
    _: ReadGuard,
) -> SensorLogRecordOut:
    """One record with its body - the raw output of that run."""
    entry = await asyncio.to_thread(store.get, record_id)
    if entry is None:
        raise NotFoundError.of("sensor_log", record_id)
    return SensorLogRecordOut.model_validate(entry)


@router.get("/{record_id}/neighbours", response_model=SensorLogNeighboursOut)
async def get_sensor_log_neighbours(
    record_id: str,
    store: SensorLogStoreDep,
    _: ReadGuard,
    failures_only: Annotated[bool, Query()] = False,
) -> SensorLogNeighboursOut:
    """The previous and next run of the same stream as one record.

    Same probe, sensor and stream as the timeline draws them, over the whole
    retention. ``failures_only`` skips passed runs; either side is null at
    the end of the history.
    """
    found = await asyncio.to_thread(
        store.neighbours, record_id, failures_only=failures_only
    )
    if found is None:
        raise NotFoundError.of("sensor_log", record_id)
    return SensorLogNeighboursOut(
        record=SensorLogSummaryOut.model_validate(found.record),
        previous=SensorLogSummaryOut.model_validate(found.previous)
        if found.previous
        else None,
        next=SensorLogSummaryOut.model_validate(found.next) if found.next else None,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
