"""Job listing, detail, live events, retry and cancel."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    BroadcasterDep,
    DbSession,
    JobServiceDep,
    PrincipalDep,
    require_permission,
)
from app.api.schemas.common import JobAccepted
from app.api.schemas.system import JobDetailOut, JobEventOut, JobSummaryOut
from app.core.permissions import Permission
from app.domain.enums import JobStatus
from app.persistence.models.inventory import (
    Deployment,
    DeploymentTarget,
    ProbeRecord,
)
from app.services.events import StreamEvent, job_topic
from app.services.jobs import JobService, ResourceRef
from app.workers.handlers import deploy_sensor

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Proxies and browsers drop an idle event stream. A comment line every few
# seconds keeps it open without inventing an event.
SSE_KEEPALIVE_SECONDS = 20.0


@router.get("", response_model=list[JobSummaryOut])
async def list_jobs(
    jobs: JobServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.JOB_READ))],
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    job_type: Annotated[str | None, Query(alias="type")] = None,
    target_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[str | None, Query()] = None,
) -> list[JobSummaryOut]:
    records = await jobs.list_jobs(
        status=job_status,
        job_type=job_type,
        target_id=target_id,
        limit=limit,
        before_id=before,
    )
    return [JobSummaryOut.model_validate(job) for job in records]


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: str,
    jobs: JobServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.JOB_READ))],
) -> JobDetailOut:
    job = await jobs.get(job_id)
    return JobDetailOut.model_validate(job)


@router.get("/{job_id}/log", response_model=list[JobEventOut])
async def job_log(
    job_id: str,
    jobs: JobServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.JOB_READ))],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> list[JobEventOut]:
    """The stored log.

    The interface fetches this first and then subscribes to the stream, so a
    page opened after a job finished shows the same thing as one that watched
    it happen.
    """
    events = await jobs.events(job_id, after_sequence=after, limit=limit)
    return [JobEventOut.model_validate(event) for event in events]


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    db: DbSession,
    jobs: JobServiceDep,
    broadcaster: BroadcasterDep,
    _: Annotated[object, Depends(require_permission(Permission.JOB_READ))],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """Server-sent events for one job.

    Replays everything after ``after`` before switching to live, so a reconnect
    cannot lose a line. Closes once the job reaches a terminal state - a stream
    that stays open on a finished job is a leak with a nice name.
    """
    # Listening first, reading second. The other way round - which is what this
    # did - leaves a window between the last stored event and the subscription:
    # the worker keeps logging while the backlog is being read, the response is
    # assembled and the ASGI server gets around to calling the generator, and
    # every line written in there is in neither half. The same window swallowed
    # the terminal status and left the stream sending keepalives at a job that
    # had long finished.
    topic = job_topic(job_id)
    queue = await broadcaster.register(topic)
    try:
        job = await jobs.get(job_id)
        backlog = await _read_backlog(jobs, job_id, after=after)
        already_finished = job.status.is_terminal
        final_status = {
            "id": job.id,
            "status": job.status.value,
            "progress": job.progress,
        }

        # Everything the stream sends is now in memory, and the session has to
        # go before the response starts. A dependency with yield is not torn
        # down until the response ends, which for a stream is as long as the
        # operator leaves the page open - and authentication has already
        # dirtied this session's last_seen_at, so the first query flushed an
        # UPDATE and opened SQLite's one write transaction. Held that long,
        # every job worker's claim waits on it and fails with "database is
        # locked".
        await db.commit()
        await db.close()
    except BaseException:
        await broadcaster.unregister(topic, queue)
        raise

    # Everything up to here is already on its way to the client; a live event
    # repeating one of those lines is a duplicate, not news.
    replayed_through = max((event["sequence"] for event in backlog), default=after)

    async def stream() -> AsyncIterator[bytes]:
        try:
            for event in backlog:
                yield _sse("job.event", event)

            if already_finished:
                yield _sse("job.status", final_status)
                yield b"event: end\ndata: {}\n\n"
                return

            # The wait for the next line is a task of its own, outliving the
            # keepalive. wait_for cancels what it waits on when it times out,
            # and a cancelled get() would take the queued line with it: the
            # stream would then lose an event on every idle interval instead of
            # sending a comment and carrying on.
            pending: asyncio.Task[StreamEvent] = asyncio.ensure_future(queue.get())
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    done, _pending = await asyncio.wait(
                        {pending}, timeout=SSE_KEEPALIVE_SECONDS
                    )
                    if not done:
                        yield b": keepalive\n\n"
                        continue

                    update = pending.result()
                    pending = asyncio.ensure_future(queue.get())

                    if (
                        update.kind == "job.event"
                        and int(update.payload.get("sequence", 0)) <= replayed_through
                    ):
                        continue

                    yield _sse(update.kind, update.payload)
                    if (
                        update.kind == "job.status"
                        and update.payload.get("status") in _TERMINAL_VALUES
                    ):
                        yield b"event: end\ndata: {}\n\n"
                        break
            finally:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
        finally:
            await broadcaster.unregister(topic, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers event streams into uselessness without this.
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{job_id}/retry", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def retry_job(
    job_id: str,
    jobs: JobServiceDep,
    db: DbSession,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.JOB_RETRY))
    ],
) -> JobAccepted:
    """Run the same job again, with the same inputs.

    A new job rather than a reset: the failed run stays in the history, which
    is what makes "it failed twice for the same reason" visible. A deployment
    retry additionally gets a deployment row of its own, restricted to the
    probes that failed - re-running into the original row overwrote the
    finished half of the history while its link kept pointing at the old job.
    """
    original = await jobs.get(job_id)
    overrides: dict[str, Any] = {}
    if (
        original.type == deploy_sensor.JOB_TYPE
        and original.payload.get("deployment_id")
        and original.status is not JobStatus.SUCCESSFUL
    ):
        result = original.result or {}
        failed = [
            str(entry.get("probe"))
            for entry in result.get("failed", [])
            if isinstance(entry, dict) and entry.get("probe")
        ]
        probes = failed or list(original.payload.get("probes", []))
        rows = (
            await db.scalars(
                select(ProbeRecord).where(ProbeRecord.nats_username.in_(probes))
            )
        ).all()
        deployment = Deployment(
            sensor_name=str(original.payload.get("sensor")),
            sensor_version=str(result.get("version") or ""),
            status=JobStatus.QUEUED,
            dry_run=bool(original.payload.get("dry_run")),
            requested_by_name=principal.username,
        )
        db.add(deployment)
        await db.flush()
        for record in rows:
            db.add(
                DeploymentTarget(
                    deployment_id=deployment.id,
                    probe_id=record.id,
                    probe_label=record.nats_username,
                )
            )
        overrides = {
            "payload_override": {
                "probes": [record.nats_username for record in rows],
                "deployment_id": deployment.id,
            },
            "resources_override": tuple(
                ResourceRef("probe", record.id) for record in rows
            ),
            "target_id": deployment.id,
            "target_label": (
                f"{original.payload.get('sensor')} → {len(rows)} probe(s)"
            ),
        }

    job = await jobs.retry(job_id, principal, **overrides)
    if overrides:
        deployment.job_id = job.id
        await db.flush()
    audit.record(
        action="job.retry",
        object_type="job",
        object_id=job.id,
        object_label=job.type,
        comment=f"retry of {job_id}",
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post("/{job_id}/cancel", response_model=JobSummaryOut)
async def cancel_job(
    job_id: str,
    jobs: JobServiceDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.JOB_CANCEL))],
) -> JobSummaryOut:
    """Ask a job to stop.

    A queued job stops immediately. A running one is asked; it checks between
    targets, so a rollout stops after the current probe rather than in the
    middle of an SSH transaction.
    """
    job = await jobs.request_cancel(job_id)
    audit.record(
        action="job.cancel",
        object_type="job",
        object_id=job.id,
        object_label=job.type,
    )
    return JobSummaryOut.model_validate(job)


_BACKLOG_PAGE = 1000


async def _read_backlog(
    jobs: JobService, job_id: str, *, after: int
) -> list[dict[str, Any]]:
    """Every stored line after ``after``, in pages.

    One query used to do this, with the service's own limit of a thousand
    quietly cutting it off - and nothing came back for the rest, because the
    stream carried on from the live end. A long rollout therefore lost the
    middle of its own log to a page size.
    """
    backlog: list[dict[str, Any]] = []
    cursor = after
    while True:
        page = await jobs.events(job_id, after_sequence=cursor, limit=_BACKLOG_PAGE)
        if not page:
            return backlog
        backlog.extend(
            JobEventOut.model_validate(event).model_dump(mode="json") for event in page
        )
        cursor = page[-1].sequence
        if len(page) < _BACKLOG_PAGE:
            return backlog


_TERMINAL_VALUES = {status.value for status in JobStatus if status.is_terminal}


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()
