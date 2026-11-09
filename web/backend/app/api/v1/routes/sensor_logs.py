"""Reading what probes reported over their return channel, and following it live."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps.common import (
    BroadcasterDep,
    DbSession,
    SensorLogStoreDep,
    require_permission,
)
from app.api.schemas.sensor_logs import (
    RecordResult,
    SensorLogBucketOut,
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
                ssid=stream.ssid,
                ok=stream.ok,
                error=stream.error,
                success_rate=stream.success_rate,
                runs_per_hour=stream.runs_per_hour,
                last_failure=SensorLogSummaryOut.model_validate(stream.last_failure)
                if stream.last_failure
                else None,
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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
