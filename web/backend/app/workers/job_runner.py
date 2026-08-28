"""The worker pool.

A handful of asyncio tasks pulling from the job table. No broker, no separate
process: the platform manages one server, and a second daemon would be one more
thing to keep alive for no benefit.

What the runner guarantees:

* a job runs only once, because claiming it is a status transition inside a
  transaction;
* a job runs only while it holds every resource it declared;
* a job that raises still ends in a terminal state with its reason recorded;
* locks are released whatever happens, including a crash, via the lease reaper;
* a probe a job worked on is asked how it looks afterwards, so the interface
  does not spend the staleness window reporting what the job has just fixed.
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
from app.persistence.models.inventory import ProbeRecord
from app.persistence.models.jobs import Job
from app.persistence.session import session_scope
from app.services import job_secrets
from app.services.events import EventBroadcaster
from app.services.jobs import JobService, declared_probe_ids
from app.services.probes import ProbeService
from app.workers.context import JobContext
from app.workers.handlers import get_definition

logger = get_logger(__name__)

IDLE_POLL_SECONDS = 1.0
REAPER_INTERVAL_SECONDS = 60.0
# How many probes to ask about themselves at once after a rollout. The same
# bound the inventory sync uses, and for the same reason: a fleet-wide job must
# not end in fifty simultaneous SSH connections.
REFRESH_CONCURRENCY = 6
# A running job this process does not know about was left behind by one that
# died. The grace period only covers the moment between the claiming UPDATE
# and its commit; anything older than that has no worker coming back for it.
_ORPHAN_GRACE = timedelta(seconds=90)


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
        # The jobs this process is actually carrying. The table cannot say so
        # on its own: a row reading "running" is a claim, not a heartbeat.
        self._active: set[str] = set()

    def hand_secrets(self, job_id: str, secrets: dict[str, str]) -> None:
        """Pass credentials to a queued job without writing them down.

        Kept as a method for the callers that already have a runner to hand;
        the values themselves live in app.services.job_secrets, because the end
        that hands them over is an API request and the end that collects them
        is a worker task, and those are not always the same object.
        """
        job_secrets.hand(job_id, secrets)

    async def start(self) -> None:
        self._stopping.clear()
        await self._recover_abandoned_jobs()
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
        claimed_id: str | None = None
        try:
            async with session_scope() as db:
                jobs = JobService(db, self._broadcaster, autocommit=True)
                job = await jobs.claim_next_queued()
                if job is None:
                    return False

                # Recorded before the next await, because from the claiming
                # UPDATE onwards the row reads running and the reaper decides
                # what is abandoned by asking this set.
                claimed_id = job.id
                self._active.add(claimed_id)

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
                    # Not an error: the job waits its turn and the interface
                    # says so. Rare now that the claim skips blocked jobs -
                    # this is the worker that lost the race for the lock.
                    await jobs.release_to_queue(job, blocking_job_id)
                    return False

                await jobs.mark_running(job)
                correlation_id = job.correlation_id

            set_correlation_id(correlation_id)
            await self._run_job(claimed_id)
            set_correlation_id(None)
            return True
        finally:
            # Also on the way out of an exception: the claim survives in the
            # table, so forgetting the job here is what lets the reaper find
            # it rather than leaving it running with nobody behind it.
            if claimed_id is not None:
                self._active.discard(claimed_id)

    async def _run_job(self, job_id: str) -> None:
        secrets = job_secrets.take(job_id)
        touched: list[str] = []
        async with session_scope() as db:
            jobs = JobService(db, self._broadcaster, autocommit=True)
            job = await db.get(Job, job_id)
            if job is None:
                return
            definition = get_definition(job.type)
            if definition is None:
                return

            # Read while the records are certainly still there. A job that
            # removes a probe is excluded by its definition, but reading this
            # up front costs nothing and does not depend on that staying true.
            if definition.refreshes_probes:
                touched = await self._probe_usernames(db, declared_probe_ids(job))

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
                # A detached job is not being cancelled here - it is carrying
                # on somewhere this process cannot see. The stack update hands
                # its work to a container and then gets shut down *by that
                # work*, so this path is reached on every successful update;
                # marking it cancelled reported the update that just worked as
                # abandoned by the operator.
                if job.status is not JobStatus.DETACHED:
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

        # Outside the session and after the locks are gone. The refresh talks
        # SSH, and the inventory sync learned what a helper call inside a
        # transaction costs: SQLite has one writer, so every other worker and
        # every API request would wait behind it. A follow-up job may take the
        # probe in the meantime and observe it mid-change - the same race the
        # sync worker lives with, and it settles the same way, because that job
        # refreshes when it ends too.
        if touched:
            await self._refresh_after_job(touched)

    async def _refresh_after_job(self, usernames: list[str]) -> None:
        """Ask the probes a job worked on how it left them.

        The job changed what the platform wants from a probe. Nothing changed
        what the platform knows about it, and until this runs the two are
        compared against each other: a sensor installed a moment ago reads as
        missing, a service just restarted reads as down, and the probe is
        reported degraded for as long as the cached observation counts as
        fresh - five minutes by default, with an alert on the dashboard.
        """
        semaphore = asyncio.Semaphore(REFRESH_CONCURRENCY)

        async def refresh(username: str) -> None:
            # A session per probe, opened only once the probe has answered -
            # refresh_after_job asks first and writes second.
            async with semaphore, session_scope() as db:
                probes = ProbeService(
                    db, self._settings, self._runtime, self._helper, self._catalog
                )
                await probes.refresh_after_job(username)

        results = await asyncio.gather(
            *(refresh(username) for username in usernames), return_exceptions=True
        )
        for username, result in zip(usernames, results, strict=True):
            if isinstance(result, BaseException):
                # The job itself is finished and recorded; failing to tidy up
                # after it must not turn a successful rollout into a traceback
                # in the log and nothing else.
                logger.warning(
                    "could not refresh probe state after a job",
                    extra={"probe": username, "error": str(result)},
                )

    @staticmethod
    async def _probe_usernames(db, probe_ids: list[str]) -> list[str]:  # type: ignore[no-untyped-def]
        """Record ids to NATS usernames - the name everything else speaks."""
        if not probe_ids:
            return []
        rows = await db.scalars(
            select(ProbeRecord.nats_username).where(ProbeRecord.id.in_(probe_ids))
        )
        return list(rows)

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
                    # Renewed before anything is reaped: the lease is there to
                    # survive a dead process, and a job still running here is
                    # the opposite of that.
                    carried = set(self._active)
                    await jobs.renew_locks(carried)
                    released = await jobs.reap_expired_locks(keep=carried)
                    if released:
                        logger.warning(
                            "released expired resource locks",
                            extra={"count": released},
                        )
                    await self._end_abandoned_jobs(db, jobs, grace=_ORPHAN_GRACE)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("lock reaper failed")

    async def _recover_abandoned_jobs(self) -> None:
        """Clear out whatever the previous process left behind.

        The platform runs a single server, so nothing can be running while
        this one starts up. A row that still says otherwise belongs to a
        process that was restarted or killed - and left on its own it holds
        the probe until the lease expires and shows up in the interface as a
        job that runs forever, with a cancel button that has nobody left to
        talk to.
        """
        async with session_scope() as db:
            jobs = JobService(db, self._broadcaster, autocommit=True)
            recovered = await self._end_abandoned_jobs(db, jobs, grace=None)
        if recovered:
            logger.warning(
                "ended jobs left running by a previous process",
                extra={"count": recovered},
            )

    async def _end_abandoned_jobs(  # type: ignore[no-untyped-def]
        self, db, jobs: JobService, *, grace: timedelta | None
    ) -> int:
        """Finish every running job no worker of this process is carrying."""
        query = select(Job).where(Job.status == JobStatus.RUNNING)
        if grace is not None:
            query = query.where(Job.started_at < datetime.now(UTC) - grace)

        ended = 0
        for job in await db.scalars(query):
            if job.id in self._active:
                continue
            # A job somebody already asked to stop is reported as cancelled,
            # not as a failure: the operator got what they asked for, however
            # late, and a red row would be a lie about what happened.
            cancelled = bool(job.cancel_requested)
            await self._mark_steps_failed(jobs, job)
            await jobs.log(
                job,
                "jobs.cancelled" if cancelled else "jobs.failed",
                level=LogLevel.WARNING if cancelled else LogLevel.ERROR,
                params={} if cancelled else {"reason": "jobs.orphaned"},
            )
            await jobs.finish(
                job,
                JobStatus.CANCELLED if cancelled else JobStatus.FAILED,
                error_code=None if cancelled else "jobs.orphaned",
                error_details=None if cancelled else "the worker did not report back",
            )
            await jobs.release_locks(job.id)
            ended += 1
        return ended


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
