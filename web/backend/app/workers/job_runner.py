"""The worker pool.

A handful of asyncio tasks pulling from the job table. No broker, no separate
process: the platform manages one server, and a second daemon would be one more
thing to keep alive for no benefit.

What the runner guarantees:

* a job runs only once, because claiming it is a status transition inside a
  transaction;
* a job runs only while it holds every resource it declared;
* a job that raises still ends in a terminal state with its reason recorded;
* locks are released whatever happens, including a crash, via the lease reaper.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger, set_correlation_id
from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.jobs import Job
from app.persistence.session import session_scope
from app.services.events import EventBroadcaster
from app.services.jobs import JobService
from app.workers.context import JobContext
from app.workers.handlers import get_definition

logger = get_logger(__name__)

IDLE_POLL_SECONDS = 1.0
REAPER_INTERVAL_SECONDS = 60.0
# A job still marked running after this long belongs to a process that is gone.
_ORPHAN_AFTER = timedelta(minutes=45)


class JobRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        broadcaster: EventBroadcaster,
        runtime: RuntimeFileStore,
        helper: ProbeHelperClient,
        catalog: SensorCatalog,
        docker: DockerAdapter,
    ) -> None:
        self._settings = settings
        self._broadcaster = broadcaster
        self._runtime = runtime
        self._helper = helper
        self._catalog = catalog
        self._docker = docker
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        # Transient values a job needs but must never store, keyed by job id.
        self._job_secrets: dict[str, dict[str, str]] = {}

    def hand_secrets(self, job_id: str, secrets: dict[str, str]) -> None:
        """Pass credentials to a queued job without writing them down.

        Used by probe onboarding, where the bootstrap SSH password exists only
        between the operator's form and the one connection that needs it.
        """
        self._job_secrets[job_id] = secrets

    async def start(self) -> None:
        self._stopping.clear()
        for index in range(self._settings.job_worker_count):
            self._tasks.append(
                asyncio.create_task(self._worker(index), name=f"job-worker-{index}")
            )
        self._tasks.append(asyncio.create_task(self._reaper(), name="lock-reaper"))
        logger.info(
            "job runner started", extra={"workers": self._settings.job_worker_count}
        )

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        logger.info("job runner stopped")

    # --- Worker loop --------------------------------------------------------

    async def _worker(self, index: int) -> None:
        while not self._stopping.is_set():
            try:
                claimed = await self._claim_and_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job worker failed", extra={"worker": index})
                claimed = False
            if not claimed:
                await asyncio.sleep(IDLE_POLL_SECONDS)

    async def _claim_and_run(self) -> bool:
        """Take one job if there is one to take. Returns whether it ran."""
        async with session_scope() as db:
            jobs = JobService(db, self._broadcaster, autocommit=True)
            job = await jobs.claim_next_queued()
            if job is None:
                return False

            definition = get_definition(job.type)
            if definition is None:
                await jobs.finish(
                    job,
                    JobStatus.FAILED,
                    error_code="jobs.unknown_type",
                    error_details=f"no handler registered for {job.type!r}",
                )
                return True

            blocking_job_id = await jobs.try_acquire_locks(job)
            if blocking_job_id is not None:
                # Not an error: the job waits its turn and the interface says so.
                await jobs.release_to_queue(job, blocking_job_id)
                return False

            await jobs.mark_running(job)
            job_id = job.id
            correlation_id = job.correlation_id

        set_correlation_id(correlation_id)
        await self._run_job(job_id)
        set_correlation_id(None)
        return True

    async def _run_job(self, job_id: str) -> None:
        secrets = self._job_secrets.pop(job_id, {})
        async with session_scope() as db:
            jobs = JobService(db, self._broadcaster, autocommit=True)
            job = await db.get(Job, job_id)
            if job is None:
                return
            definition = get_definition(job.type)
            if definition is None:
                return

            context = JobContext(
                job=job,
                jobs=jobs,
                db=db,
                settings=self._settings,
                runtime=self._runtime,
                helper=self._helper,
                catalog=self._catalog,
                docker=self._docker,
                secrets=secrets,
            )

            await jobs.log(
                job,
                "jobs.started",
                params={"type": job.type, "target": job.target_label or ""},
            )

            try:
                result = await definition.handler(context)
            except AppError as error:
                await self._mark_steps_failed(jobs, job)
                await jobs.log(
                    job,
                    "jobs.failed",
                    level=LogLevel.ERROR,
                    params={"reason": error.code, **error.params},
                    raw=error.details,
                )
                await jobs.finish(
                    job,
                    JobStatus.FAILED,
                    error_code=error.code,
                    error_params=error.params,
                    error_details=error.details,
                )
            except asyncio.CancelledError:
                await jobs.finish(job, JobStatus.CANCELLED)
                raise
            except Exception as exc:
                await self._mark_steps_failed(jobs, job)
                logger.exception("job handler raised", extra={"job_id": job.id})
                await jobs.log(
                    job,
                    "jobs.failed",
                    level=LogLevel.ERROR,
                    params={"reason": "internal.unexpected"},
                    raw=f"{type(exc).__name__}: {exc}",
                )
                await jobs.finish(
                    job,
                    JobStatus.FAILED,
                    error_code="internal.unexpected",
                    error_details=f"{type(exc).__name__}: {exc}",
                )
            else:
                status = _outcome_status(job, result)
                if status is JobStatus.SUCCESSFUL:
                    await self._mark_remaining_steps(jobs, job)
                else:
                    # A handler that returns with failures must not leave every
                    # step green; the step list is how an operator sees where a
                    # rollout stopped.
                    await self._mark_steps_failed(jobs, job)
                await jobs.log(
                    job,
                    _OUTCOME_MESSAGES.get(status, "jobs.finished"),
                    level=LogLevel.INFO
                    if status is JobStatus.SUCCESSFUL
                    else LogLevel.WARNING,
                    params={"status": status.value},
                )
                await jobs.finish(
                    job,
                    status,
                    result=result,
                    # Without a code the detail page shows a failed job and no
                    # explanation. The per-target reasons stay in the result.
                    error_code=_outcome_error_code(status),
                    error_params=(
                        {"failed": len(result.get("failed") or [])}
                        if status is not JobStatus.SUCCESSFUL
                        else None
                    ),
                )
            finally:
                await jobs.release_locks(job.id)

    @staticmethod
    async def _mark_steps_failed(jobs: JobService, job: Job) -> None:
        for step in job.steps:
            if step.status is JobStepStatus.RUNNING:
                await jobs.finish_step(job, step.name, JobStepStatus.FAILED)
            elif step.status is JobStepStatus.PENDING:
                await jobs.finish_step(job, step.name, JobStepStatus.SKIPPED)

    @staticmethod
    async def _mark_remaining_steps(jobs: JobService, job: Job) -> None:
        for step in job.steps:
            if step.status in {JobStepStatus.RUNNING, JobStepStatus.PENDING}:
                await jobs.finish_step(job, step.name, JobStepStatus.SUCCEEDED)

    # --- Housekeeping -------------------------------------------------------

    async def _reaper(self) -> None:
        """Free locks whose worker never returned, and unstick blocked jobs."""
        while not self._stopping.is_set():
            await asyncio.sleep(REAPER_INTERVAL_SECONDS)
            try:
                async with session_scope() as db:
                    jobs = JobService(db, self._broadcaster, autocommit=True)
                    released = await jobs.reap_expired_locks()
                    if released:
                        logger.warning(
                            "released expired resource locks",
                            extra={"count": released},
                        )
                    await self._fail_orphaned_jobs(db, jobs)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("lock reaper failed")

    async def _fail_orphaned_jobs(self, db, jobs: JobService) -> None:  # type: ignore[no-untyped-def]
        """A job left running by a killed process is stuck forever otherwise."""
        cutoff = datetime.now(UTC) - _ORPHAN_AFTER
        orphans = await db.scalars(
            select(Job).where(Job.status == JobStatus.RUNNING, Job.started_at < cutoff)
        )
        for job in orphans:
            await jobs.finish(
                job,
                JobStatus.FAILED,
                error_code="jobs.orphaned",
                error_details="the worker did not report back within the lease",
            )
            await jobs.release_locks(job.id)


_OUTCOME_MESSAGES: dict[JobStatus, str] = {
    JobStatus.SUCCESSFUL: "jobs.finished",
    JobStatus.PARTIALLY_SUCCESSFUL: "jobs.finished_partial",
    JobStatus.FAILED: "jobs.failed",
    JobStatus.CANCELLED: "jobs.cancelled",
}


def _outcome_error_code(status: JobStatus) -> str | None:
    """A failed job needs a code, or the interface has nothing to show."""
    if status is JobStatus.FAILED:
        return "jobs.all_targets_failed"
    if status is JobStatus.PARTIALLY_SUCCESSFUL:
        return "jobs.some_targets_failed"
    return None


def _outcome_status(job: Job, result: dict[str, object]) -> JobStatus:
    if job.cancel_requested:
        return JobStatus.CANCELLED
    failed = result.get("failed")
    succeeded = result.get("succeeded")
    if isinstance(failed, list) and failed:
        if isinstance(succeeded, list) and succeeded:
            return JobStatus.PARTIALLY_SUCCESSFUL
        return JobStatus.FAILED
    return JobStatus.SUCCESSFUL
