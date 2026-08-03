"""Job listing, detail, live events, retry and cancel."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

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
from app.services.events import StreamEvent, job_topic

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
    job = await jobs.get(job_id)
    backlog = [
        JobEventOut.model_validate(event).model_dump(mode="json")
        for event in await jobs.events(job_id, after_sequence=after)
    ]
    already_finished = job.status.is_terminal
    final_status = {"id": job.id, "status": job.status.value, "progress": job.progress}

    # Everything the stream sends is now in memory, and the session has to go
    # before the response starts. A dependency with yield is not torn down until
    # the response ends, which for a stream is as long as the operator leaves
    # the page open - and authentication has already dirtied this session's
    # last_seen_at, so the first query flushed an UPDATE and opened SQLite's one
    # write transaction. Held that long, every job worker's claim waits on it
    # and fails with "database is locked".
    await db.commit()
    await db.close()

    async def stream() -> AsyncIterator[bytes]:
        for event in backlog:
            yield _sse("job.event", event)

        if already_finished:
            yield _sse("job.status", final_status)
            yield b"event: end\ndata: {}\n\n"
            return

        subscription = broadcaster.subscribe(job_topic(job_id))
        iterator = subscription.__aiter__()
        # The wait for the next line is a task of its own, outliving the
        # keepalive. wait_for cancels what it waits on when it times out, and a
        # cancelled __anext__ runs the subscription's finally and closes it for
        # good: the stream would then die on the first idle interval instead of
        # sending a comment and carrying on.
        pending: asyncio.Task[StreamEvent] = asyncio.ensure_future(iterator.__anext__())
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

                try:
                    update = pending.result()
                except StopAsyncIteration:  # the broadcaster went away
                    break
                pending = asyncio.ensure_future(iterator.__anext__())

                yield _sse(update.kind, update.payload)
                if (
                    update.kind == "job.status"
                    and update.payload.get("status") in _TERMINAL_VALUES
                ):
                    yield b"event: end\ndata: {}\n\n"
                    break
        finally:
            # StopAsyncIteration is suppressed rather than allowed out: raised
            # inside an async generator it becomes a RuntimeError the ASGI
            # server reports as a failed application.
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
            await subscription.aclose()

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
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.JOB_RETRY))
    ],
) -> JobAccepted:
    """Run the same job again, with the same inputs.

    A new job rather than a reset: the failed run stays in the history, which
    is what makes "it failed twice for the same reason" visible.
    """
    job = await jobs.retry(job_id, principal)
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


_TERMINAL_VALUES = {status.value for status in JobStatus if status.is_terminal}


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()
