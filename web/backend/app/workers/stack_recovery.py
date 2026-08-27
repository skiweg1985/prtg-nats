"""Finish the update that replaced the process which started it.

A stack update ends by recreating the containers, and one of them is this one.
The job driving it therefore cannot report its own outcome: by the time the
updater knows whether it worked, the process that would have written that down
no longer exists.

What survives is the container. It is created without AutoRemove precisely so
that it still holds its exit code and its log when a new process comes looking,
and the stack_update row is what tells that process where to look.

This runs before the job runner starts. The runner's own recovery ends every
job still marked running, on the correct assumption that nothing survives a
restart - so the outcome has to be on record before it ever looks.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.infrastructure.docker import DockerAdapter
from app.persistence.models.jobs import Job
from app.persistence.models.updates import StackUpdate
from app.persistence.session import session_scope
from app.services.events import EventBroadcaster
from app.services.jobs import JobService

logger = get_logger(__name__)

# The steps the updater performs on the far side of the handover. Named here
# as well as in the handler because this process has to close them out without
# having watched them happen.
SETTLE_STEP = "settle"
RECREATE_STEP = "recreate"


async def settle_interrupted_update(
    settings: Settings,
    docker: DockerAdapter,
    broadcaster: EventBroadcaster,
) -> None:
    """Record the outcome of an update that outlived its own process."""
    async with session_scope() as db:
        pending = list(
            await db.scalars(select(StackUpdate).where(StackUpdate.settled.is_(False)))
        )
        if not pending:
            return

        jobs = JobService(db, broadcaster, autocommit=True)
        for record in pending:
            job = await db.get(Job, record.job_id)
            if job is None:
                # The job was deleted while the update ran. Nothing to report
                # to, so close the record rather than carrying it forever.
                record.settled = True
                continue
            await _settle_one(jobs, docker, record, job)
        await db.flush()


async def _settle_one(
    jobs: JobService,
    docker: DockerAdapter,
    record: StackUpdate,
    job: Job,
) -> None:
    if job.status.is_terminal:
        record.settled = True
        return

    exit_code = await _exit_code(docker, record)

    # Whatever the updater said after the last line this job already carries.
    # The operator watched the build live and then lost the connection; this
    # is the part they missed, not a repetition of what they read.
    tail = ""
    if record.container_id:
        whole = await docker.container_logs(record.container_id)
        # log_cursor counts the lines the job already carries, so this is
        # exactly the part the operator lost when the connection dropped.
        tail = "\n".join(whole.splitlines()[record.log_cursor :])
    # The phase markers have done their work already - the steps they drive
    # were advanced by the process that watched them live. Here they would
    # only be noise in the technical detail.
    body = "\n".join(
        line for line in tail.splitlines() if not line.strip().startswith("::phase")
    ).strip()
    if body:
        headline = next(
            (line.strip() for line in reversed(body.splitlines()) if line.strip()),
            "",
        )
        await jobs.log(
            job,
            "jobs.stack.updater_output",
            level=LogLevel.INFO,
            step=RECREATE_STEP,
            params={"line": headline[:200]},
            raw=body[-8000:],
        )

    if exit_code is None:
        # The container is gone and never reported. It cannot be asked again,
        # and claiming either outcome would be a guess about whether this
        # installation was updated.
        await jobs.log(
            job,
            "jobs.stack.outcome_unknown",
            level=LogLevel.ERROR,
            params={"container": record.container_id[:12]},
        )
        await _close_steps(jobs, job, ok=False)
        await jobs.finish(
            job,
            JobStatus.FAILED,
            error_code="stack.update_failed",
            error_details=(
                "the updater container could not be found after the restart, "
                "so its outcome is unknown"
            ),
        )
    elif exit_code == 0:
        await jobs.log(
            job, "jobs.stack.updated", params={"commit": record.commit_to[:12]}
        )
        await _close_steps(jobs, job, ok=True)
        await jobs.finish(
            job,
            JobStatus.SUCCESSFUL,
            result={"commit": record.commit_to, "branch": record.branch},
        )
    else:
        await jobs.log(
            job,
            "jobs.stack.update_failed",
            level=LogLevel.ERROR,
            params={"code": str(exit_code)},
        )
        await _close_steps(jobs, job, ok=False)
        await jobs.finish(
            job,
            JobStatus.FAILED,
            error_code="stack.update_failed",
            error_details=f"the updater exited with code {exit_code}",
        )

    await jobs.release_locks(job.id)
    record.settled = True

    # Only now: the container was the only copy of what happened, and it stays
    # until that has been written down somewhere an operator can read it.
    if record.container_id:
        try:
            await docker.remove_container(record.container_id)
        except Exception:
            # A leftover container is untidy; failing the recovery over one
            # would leave the job hanging, which is worse.
            logger.warning(
                "could not remove the updater container",
                extra={"container": record.container_id[:12]},
            )

    logger.info(
        "settled an interrupted stack update",
        extra={"job": job.id, "exit_code": exit_code},
    )


async def _exit_code(docker: DockerAdapter, record: StackUpdate) -> int | None:
    """The updater's exit code, or None if it cannot be established.

    A container still running is not an unknown outcome - it means this process
    came back before the updater finished, which happens when compose replaces
    the API container and then waits on the rest. Waiting it out is the point.
    """
    if not record.container_id or not docker.available:
        return None
    try:
        code = await docker.container_exit_code(record.container_id)
        if code is None:
            return await docker.wait_container(record.container_id, timeout=900.0)
        return code
    except Exception:
        logger.warning(
            "could not read the updater's exit code",
            extra={"container": record.container_id[:12]},
        )
        return None


async def _close_steps(jobs: JobService, job: Job, *, ok: bool) -> None:
    """Close the steps the handover left open, truthfully.

    The steps before the handover already have their real outcome; only the
    ones nobody was there to watch are set here.
    """
    for step in job.steps:
        if step.status in {JobStepStatus.RUNNING, JobStepStatus.PENDING}:
            await jobs.finish_step(
                job,
                step.name,
                JobStepStatus.SUCCEEDED if ok else JobStepStatus.FAILED,
            )
