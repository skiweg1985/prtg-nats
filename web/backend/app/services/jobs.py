"""Create, inspect and control jobs.

A job is how the platform talks about work that takes time or can fail. The
rules that make it trustworthy live here: a job declares what it touches before
it starts, it never runs while another job holds one of those resources, and
every line it emits is stored before it is broadcast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.ids import new_id
from app.core.logging import get_correlation_id, get_logger
from app.core.redaction import redact, redact_text
from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.persistence.models.jobs import Job, JobEvent, JobStep, ResourceLock
from app.services.auth import Principal
from app.services.events import (
    JOBS_TOPIC,
    EventBroadcaster,
    StreamEvent,
    job_topic,
)

logger = get_logger(__name__)

# How long a lock survives without its worker. Long enough that a slow sensor
# install is not interrupted, short enough that a crashed process does not lock
# a probe out for the rest of the day.
LOCK_LEASE = timedelta(minutes=30)

# How far down the queue a poll looks for a job it can actually run. A head of
# the queue blocked on one busy probe must not stop everything behind it.
CLAIM_CANDIDATES = 20

BLOCKED_RESOURCE_BUSY = "jobs.blocked.resource_busy"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """Something a job needs exclusive use of."""

    type: str  # "probe" | "nats" | "certificate" | "credential"
    id: str

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True, slots=True)
class JobRequest:
    type: str
    steps: tuple[str, ...]
    resources: tuple[ResourceRef, ...] = ()
    target_type: str | None = None
    target_id: str | None = None
    target_label: str | None = None
    payload: dict[str, Any] | None = None
    trigger: str = "user"


class JobService:
    def __init__(
        self,
        session: AsyncSession,
        broadcaster: EventBroadcaster,
        *,
        autocommit: bool = False,
    ) -> None:
        """``autocommit`` is for the worker's own session.

        A handler runs for minutes; holding one write transaction across it
        would hold SQLite's write lock across the whole platform for exactly
        that long. The worker therefore commits after every state change, and
        the log becomes durable line by line - which is also what a reader
        polling the job table expects to see. API requests keep the default:
        their transaction belongs to the request.
        """
        self._db = session
        self._events = broadcaster
        self._autocommit = autocommit

    async def _persist(self) -> None:
        await self._db.flush()
        if self._autocommit:
            await self._db.commit()

    # --- Creating -----------------------------------------------------------

    async def create(
        self, request: JobRequest, principal: Principal | None = None
    ) -> Job:
        job = Job(
            id=new_id(),
            type=request.type,
            status=JobStatus.QUEUED,
            target_type=request.target_type,
            target_id=request.target_id,
            target_label=request.target_label,
            trigger=request.trigger,
            requested_by_id=None if principal is None else principal.user_id,
            requested_by_name=None if principal is None else principal.username,
            correlation_id=get_correlation_id(),
            # Payload is inventory, never secrets: transient credentials are
            # handed to the runner in memory and never written down.
            # Resources are recorded alongside so the runner knows what to take
            # without re-deriving it from the payload.
            payload={
                **redact(dict(request.payload or {})),
                "_resources": [str(resource) for resource in request.resources],
            },
        )
        # Assigned before the job is persisted, not added separately afterwards:
        # once the job has an identity, reading `job.steps` would issue a lazy
        # load, and the first status broadcast happens outside a greenlet where
        # that raises. Populating the collection up front keeps it in memory.
        job.steps = [
            JobStep(position=position, name=name)
            for position, name in enumerate(request.steps)
        ]
        self._db.add(job)
        await self._db.flush()
        return job

    async def retry(
        self,
        job_id: str,
        principal: Principal | None = None,
        *,
        payload_override: dict[str, Any] | None = None,
        resources_override: tuple[ResourceRef, ...] | None = None,
        target_id: str | None = None,
        target_label: str | None = None,
    ) -> Job:
        """Run the job again - by default with the same inputs.

        The overrides exist for jobs whose payload names sibling records: a
        deployment retry must point at a fresh deployment row and only the
        probes that failed, or it overwrites the finished half of the history.
        The caller that knows those records passes the corrected pieces.
        """
        original = await self.get(job_id)
        if not original.status.is_terminal:
            raise ConflictError(
                params={"job_id": job_id, "status": original.status.value},
                details="only a finished job can be retried",
            )
        resources = resources_override or tuple(
            _parse_resource(entry) for entry in original.payload.get("_resources", [])
        )
        payload = {
            key: value for key, value in original.payload.items() if key != "_resources"
        }
        if payload_override:
            payload.update(payload_override)
        retry = await self.create(
            JobRequest(
                type=original.type,
                steps=tuple(step.name for step in original.steps),
                resources=resources,
                target_type=original.target_type,
                target_id=target_id or original.target_id,
                target_label=target_label or original.target_label,
                payload=payload,
                trigger="user",
            ),
            principal,
        )
        retry.retry_of_job_id = original.id
        await self._db.flush()
        return retry

    async def detach(self, job: Job) -> None:
        """Hand the job over to something that outlives this process.

        Not terminal: the outcome is still to come, from whoever picks the
        record up next. What it buys is that the shutdown this job is about to
        cause does not get recorded as somebody cancelling it.
        """
        job.status = JobStatus.DETACHED
        await self._persist()
        await self._publish_status(job)

    async def request_cancel(self, job_id: str) -> Job:
        job = await self.get(job_id)
        if job.status.is_terminal:
            raise ConflictError(
                params={"job_id": job_id, "status": job.status.value},
                details="the job has already finished",
            )
        if job.status is JobStatus.QUEUED:
            # Nothing has started, so it can go straight to cancelled.
            await self.finish(job, JobStatus.CANCELLED)
            return job
        job.cancel_requested = True
        await self._db.flush()
        return job

    # --- Reading ------------------------------------------------------------

    async def get(self, job_id: str) -> Job:
        job = await self._db.get(Job, job_id)
        if job is None:
            raise NotFoundError.of("job", job_id)
        return job

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
        before_id: str | None = None,
    ) -> list[Job]:
        query = select(Job).order_by(Job.id.desc()).limit(min(limit, 200))
        if status is not None:
            query = query.where(Job.status == status)
        if job_type is not None:
            query = query.where(Job.type == job_type)
        if target_id is not None:
            query = query.where(Job.target_id == target_id)
        if before_id is not None:
            query = query.where(Job.id < before_id)
        return list(await self._db.scalars(query))

    async def events(
        self, job_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> list[JobEvent]:
        return list(
            await self._db.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.sequence > after_sequence)
                .order_by(JobEvent.sequence)
                .limit(limit)
            )
        )

    async def claim_next_queued(self) -> Job | None:
        """Take ownership of the oldest runnable queued job, or return None.

        The claim is a conditional UPDATE, not a SELECT followed by one: with
        several workers polling the same table, two of them read the same
        queued row before either writes, and the job runs twice. Only the
        worker whose UPDATE matches a row gets to proceed.

        A job whose resources are taken is skipped rather than claimed and
        handed straight back. Claiming it writes the row twice per poll and
        per worker, so a job that stays blocked for an hour becomes an hour
        of pointless writes - and on SQLite one writer at a time means the
        rest of the platform waits behind it.
        """
        candidates = (
            await self._db.execute(
                select(
                    Job.id, Job.payload, Job.blocked_reason_key, Job.blocked_by_job_id
                )
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.id)
                .limit(CLAIM_CANDIDATES)
            )
        ).all()
        if not candidates:
            return None

        # Columns rather than entities: this runs four times a second per
        # worker, and loading whole jobs with their steps to look at one
        # payload field is the kind of cost that only shows up in production.
        busy = await self._busy_resources()
        for job_id, payload, reason_key, blocked_by in candidates:
            blocking_job_id = _blocking_job_id(job_id, payload, busy)
            if blocking_job_id is not None:
                if reason_key != BLOCKED_RESOURCE_BUSY or blocked_by != blocking_job_id:
                    await self._note_blocked(job_id, blocking_job_id)
                continue

            claimed = await self._db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING, started_at=datetime.now(UTC))
            )
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                # Another worker got there first. Try the next candidate; its
                # own claim decides whether this worker has anything to do.
                continue

            job: Job | None = await self._db.get(Job, job_id)
            return job
        return None

    async def _note_blocked(self, job_id: str, blocking_job_id: str) -> None:
        """Record what a queued job is waiting for.

        The caller only gets here when the reason changed: every worker sees
        the same blocked job on every poll, and rewriting the same row would
        put back exactly the write loop that skipping the job avoids.
        """
        job = await self._db.get(Job, job_id)
        if job is None:
            return
        job.blocked_reason_key = BLOCKED_RESOURCE_BUSY
        job.blocked_by_job_id = blocking_job_id
        await self._persist()
        await self._publish_status(job)

    # --- Locking ------------------------------------------------------------

    async def _busy_resources(self) -> dict[tuple[str, str], str]:
        """Every resource currently spoken for, mapped to the job holding it."""
        now = datetime.now(UTC)
        locks = await self._db.scalars(select(ResourceLock))
        return {
            (lock.resource_type, lock.resource_id): lock.job_id
            for lock in locks
            if lock.expires_at > now
        }

    async def try_acquire_locks(self, job: Job) -> str | None:
        """Take every resource the job declared, or none of them.

        Returns the id of the job that is in the way, or None on success. The
        unique constraint does the work: two workers racing for the same probe
        cannot both win, whatever the timing.
        """
        resources = [
            _parse_resource(entry) for entry in job.payload.get("_resources", [])
        ]
        if not resources:
            return None

        now = datetime.now(UTC)
        held = await self._db.scalars(
            select(ResourceLock).where(
                ResourceLock.resource_type.in_([r.type for r in resources])
            )
        )
        blocking = {
            (lock.resource_type, lock.resource_id): lock
            for lock in held
            if lock.expires_at > now and lock.job_id != job.id
        }
        for resource in resources:
            existing = blocking.get((resource.type, resource.id))
            if existing is not None:
                return existing.job_id

        for resource in resources:
            self._db.add(
                ResourceLock(
                    resource_type=resource.type,
                    resource_id=resource.id,
                    job_id=job.id,
                    acquired_at=now,
                    expires_at=now + LOCK_LEASE,
                )
            )
        try:
            await self._persist()
        except IntegrityError:
            # Lost the race between the check and the insert. Roll back to a
            # clean state and let the caller keep the job queued.
            await self._db.rollback()
            return "unknown"
        return None

    async def release_locks(self, job_id: str) -> None:
        locks = await self._db.scalars(
            select(ResourceLock).where(ResourceLock.job_id == job_id)
        )
        for lock in locks:
            await self._db.delete(lock)
        await self._persist()

    async def renew_locks(self, job_ids: set[str]) -> int:
        """Push the lease out for jobs a worker is still carrying.

        The lease exists to free a probe from a process that died, not to put
        a time limit on the work itself. A sensor rollout across several probes
        outlives ``LOCK_LEASE`` without anything being wrong, and without this
        the reaper would hand its probe to the next job while the SSH
        transaction is still open.
        """
        if not job_ids:
            return 0
        result = await self._db.execute(
            update(ResourceLock)
            .where(ResourceLock.job_id.in_(job_ids))
            .values(expires_at=datetime.now(UTC) + LOCK_LEASE)
        )
        await self._persist()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def reap_expired_locks(self, *, keep: set[str] | None = None) -> int:
        """Free resources whose worker never came back.

        ``keep`` names the jobs this process is carrying. They are spared even
        when their lease reads expired: renewing and reaping run in the same
        pass, and a lock whose renewal lost that race still has a worker behind
        it.
        """
        now = datetime.now(UTC)
        query = select(ResourceLock).where(ResourceLock.expires_at <= now)
        if keep:
            query = query.where(ResourceLock.job_id.notin_(keep))
        expired = list(await self._db.scalars(query))
        for lock in expired:
            await self._db.delete(lock)
        await self._persist()
        return len(expired)

    # --- State transitions --------------------------------------------------

    async def mark_running(self, job: Job) -> None:
        """Announce a job the claim already moved to running."""
        job.blocked_reason_key = None
        job.blocked_by_job_id = None
        await self._persist()
        await self._publish_status(job)

    async def release_to_queue(self, job: Job, blocking_job_id: str) -> None:
        """Hand a claimed job back because a resource it needs is taken.

        It returns to `queued` carrying the reason, so an operator sees why it
        is waiting rather than a row that simply sits there.
        """
        job.status = JobStatus.QUEUED
        job.started_at = None
        job.blocked_reason_key = BLOCKED_RESOURCE_BUSY
        job.blocked_by_job_id = blocking_job_id
        await self._persist()
        await self._publish_status(job)

    async def finish(
        self,
        job: Job,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_params: dict[str, Any] | None = None,
        error_details: str | None = None,
    ) -> None:
        job.status = status
        job.finished_at = datetime.now(UTC)
        job.progress = 100 if status is JobStatus.SUCCESSFUL else job.progress
        job.result = redact(result) if result is not None else None
        job.error_code = error_code
        job.error_params = redact(error_params) if error_params else None
        job.error_details = redact_text(error_details) if error_details else None
        await self._persist()
        await self._publish_status(job)

    async def start_step(self, job: Job, name: str) -> JobStep | None:
        step = next((entry for entry in job.steps if entry.name == name), None)
        if step is None:
            return None
        # Moving on means the previous step finished. Without this every step
        # stays "running" until the job ends, and the failure handler then
        # marks all of them failed - including the ones that worked.
        for previous in job.steps:
            if previous.position < step.position and (
                previous.status is JobStepStatus.RUNNING
            ):
                previous.status = JobStepStatus.SUCCEEDED
                previous.finished_at = datetime.now(UTC)
        step.status = JobStepStatus.RUNNING
        step.started_at = datetime.now(UTC)
        job.current_step = name
        job.progress = _progress_for(job, name)
        await self._persist()
        await self._publish_status(job)
        return step

    async def finish_step(self, job: Job, name: str, status: JobStepStatus) -> None:
        step = next((entry for entry in job.steps if entry.name == name), None)
        if step is None:
            return
        step.status = status
        step.finished_at = datetime.now(UTC)
        await self._persist()
        await self._publish_status(job)

    # --- Logging ------------------------------------------------------------

    async def log(
        self,
        job: Job,
        code: str,
        *,
        level: LogLevel = LogLevel.INFO,
        params: dict[str, Any] | None = None,
        step: str | None = None,
        target: str | None = None,
        raw: str | None = None,
    ) -> JobEvent:
        """One line of the live log.

        ``code`` and ``params`` are what the browser translates; ``raw`` is the
        untranslated technical output kept behind a disclosure control.
        """
        sequence = await self._next_sequence(job.id)
        event = JobEvent(
            job_id=job.id,
            sequence=sequence,
            ts=datetime.now(UTC),
            level=level,
            step=step or job.current_step,
            target=target,
            code=code,
            params=redact(params or {}),
            raw=redact_text(raw) if raw else None,
        )
        self._db.add(event)
        await self._persist()

        await self._events.publish(
            StreamEvent(
                topic=job_topic(job.id),
                kind="job.event",
                payload={
                    "id": event.id,
                    "sequence": event.sequence,
                    "ts": event.ts.isoformat(),
                    "level": event.level.value,
                    "step": event.step,
                    "target": event.target,
                    "code": event.code,
                    "params": event.params,
                    "raw": event.raw,
                },
            )
        )
        return event

    async def _next_sequence(self, job_id: str) -> int:
        current = await self._db.scalar(
            select(JobEvent.sequence)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.sequence.desc())
            .limit(1)
        )
        return (current or 0) + 1

    async def _publish_status(self, job: Job) -> None:
        payload = {
            "id": job.id,
            "type": job.type,
            "status": job.status.value,
            "progress": job.progress,
            "current_step": job.current_step,
            "target_label": job.target_label,
            "blocked_reason_key": job.blocked_reason_key,
            "blocked_by_job_id": job.blocked_by_job_id,
            "error_code": job.error_code,
            "steps": [
                {"name": step.name, "status": step.status.value} for step in job.steps
            ],
        }
        await self._events.publish(
            StreamEvent(topic=job_topic(job.id), kind="job.status", payload=payload)
        )
        await self._events.publish(
            StreamEvent(topic=JOBS_TOPIC, kind="job.status", payload=payload)
        )


def _blocking_job_id(
    job_id: str, payload: dict[str, Any], busy: dict[tuple[str, str], str]
) -> str | None:
    """The job standing in the way, or None if every resource is free."""
    for entry in payload.get("_resources", []):
        resource = _parse_resource(entry)
        holder = busy.get((resource.type, resource.id))
        if holder is not None and holder != job_id:
            return holder
    return None


def _parse_resource(entry: str) -> ResourceRef:
    resource_type, _, resource_id = entry.partition(":")
    return ResourceRef(type=resource_type, id=resource_id)


def declared_probe_ids(job: Job) -> list[str]:
    """The probe records a job declared exclusive use of.

    Here rather than at the caller so how a resource is written down stays in
    this module: it is a string in the payload because a job has to know what
    it needs before it can be claimed, and that is nobody else's business.
    """
    return [
        resource.id
        for resource in (
            _parse_resource(entry) for entry in job.payload.get("_resources", [])
        )
        if resource.type == "probe"
    ]


def _progress_for(job: Job, step_name: str) -> int:
    names = [step.name for step in job.steps]
    if step_name not in names or not names:
        return job.progress
    return int(names.index(step_name) / len(names) * 100)
