"""An update outlives the process that started it. The job has to as well.

`docker compose up` replaces prtg-nats-web-api partway through the update, so
the job driving it dies mid-sentence. Two things then have to hold, and they
pull in opposite directions:

* The runner's startup recovery ends every job still marked running. It is
  right to: the platform runs one server, so a job that says "running" while
  the process starts belongs to a process that was killed. Without that, a
  crashed worker leaves a probe locked and a job spinning forever.

* The update job is the one case where that assumption is false. Marked
  failed, it would report a working update as broken - and it would do so on
  every single successful update, which is the worst possible failure mode for
  a feature whose whole job is to be trusted with the installation.

The status is what keeps both true. A detached job is not running, so the
recovery does not see it; and the outcome is recorded before the runner ever
starts.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.enums import JobStatus
from app.persistence.models.jobs import Job, JobEvent
from app.persistence.models.updates import StackUpdate
from app.services.events import EventBroadcaster
from app.services.jobs import JobRequest, JobService, ResourceRef
from app.workers.job_runner import JobRunner
from app.workers.stack_recovery import _settle_one


class FinishedUpdater:
    """A container that ran to completion and still holds its answer."""

    def __init__(self, *, exit_code: int, log: str = "") -> None:
        self.available = True
        self.exit_code = exit_code
        self.log = log
        self.removed: list[str] = []

    async def container_exit_code(self, container_id: str) -> int | None:
        return self.exit_code

    async def container_logs(self, container_id: str) -> str:
        return self.log

    async def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)


class VanishedUpdater:
    """The container is gone, so what it did cannot be established."""

    available = True

    async def container_exit_code(self, container_id: str) -> int | None:
        return None

    async def wait_container(self, container_id: str, *, timeout: float) -> int:
        raise RuntimeError("no such container")

    async def container_logs(self, container_id: str) -> str:
        return ""

    async def remove_container(self, container_id: str) -> None:
        return None


async def _detached_update(
    db: AsyncSession, *, log_cursor: int = 0
) -> tuple[Job, StackUpdate]:
    jobs = JobService(db, EventBroadcaster())
    job = await jobs.create(
        JobRequest(
            type="stack.update",
            steps=("preflight", "build", "recreate", "settle"),
            resources=(ResourceRef("stack", "installation"),),
        )
    )
    job.status = JobStatus.DETACHED
    record = StackUpdate(
        job_id=job.id,
        container_id="c0ffee1234",
        commit_from="a" * 40,
        commit_to="b" * 40,
        branch="main",
        checkout_dir="/opt/prtg-nats-server",
        log_cursor=log_cursor,
    )
    db.add(record)
    await db.flush()
    return job, record


async def test_the_runner_leaves_a_detached_job_alone(
    db: AsyncSession, settings: Settings
) -> None:
    """The regression this whole status exists to prevent.

    Marked failed here, every successful update would come back reported as a
    failure - and an operator would have no way to tell that from a real one.
    """
    job, _ = await _detached_update(db)
    runner = JobRunner(
        settings=settings,
        broadcaster=EventBroadcaster(),
        runtime=None,  # type: ignore[arg-type]
        helper=None,  # type: ignore[arg-type]
        catalog=None,  # type: ignore[arg-type]
        docker=None,  # type: ignore[arg-type]
    )
    jobs = JobService(db, EventBroadcaster())

    ended = await runner._end_abandoned_jobs(db, jobs, grace=None)

    assert ended == 0
    await db.refresh(job)
    assert job.status is JobStatus.DETACHED


async def test_a_job_left_running_is_still_ended(
    db: AsyncSession, settings: Settings
) -> None:
    """The opposite case, unchanged: nothing else survives a restart."""
    jobs = JobService(db, EventBroadcaster())
    job = await jobs.create(
        JobRequest(type="sensor.deploy", steps=("activate",), resources=())
    )
    job.status = JobStatus.RUNNING
    await db.flush()
    runner = JobRunner(
        settings=settings,
        broadcaster=EventBroadcaster(),
        runtime=None,  # type: ignore[arg-type]
        helper=None,  # type: ignore[arg-type]
        catalog=None,  # type: ignore[arg-type]
        docker=None,  # type: ignore[arg-type]
    )

    ended = await runner._end_abandoned_jobs(db, jobs, grace=None)

    assert ended == 1
    await db.refresh(job)
    assert job.status is JobStatus.FAILED


async def test_a_successful_update_is_recorded_after_the_restart(
    db: AsyncSession,
) -> None:
    job, record = await _detached_update(db)
    docker: Any = FinishedUpdater(exit_code=0, log="Building...\nStack recreated.\n")
    jobs = JobService(db, EventBroadcaster())

    await _settle_one(jobs, docker, record, job)

    assert job.status is JobStatus.SUCCESSFUL
    assert record.settled is True
    # The container was the only copy of what happened, so it goes only after
    # the outcome is written down.
    assert docker.removed == ["c0ffee1234"]


async def test_only_the_unseen_part_of_the_log_is_carried_over(
    db: AsyncSession,
) -> None:
    """The operator watched the build live and then lost the connection.

    Copying the whole log across would repeat everything they already read,
    which makes a job log twice as long and half as trustworthy.
    """
    job, record = await _detached_update(db, log_cursor=2)
    docker: Any = FinishedUpdater(
        exit_code=0, log="line one\nline two\nline three\nline four\n"
    )
    jobs = JobService(db, EventBroadcaster())

    await _settle_one(jobs, docker, record, job)

    events = list(await db.scalars(select(JobEvent).where(JobEvent.job_id == job.id)))
    carried = "\n".join(event.raw or "" for event in events)
    assert "line three" in carried
    assert "line four" in carried
    assert "line one" not in carried


async def test_a_failed_update_is_reported_as_one(db: AsyncSession) -> None:
    job, record = await _detached_update(db)
    docker: Any = FinishedUpdater(exit_code=1, log="something went wrong\n")
    jobs = JobService(db, EventBroadcaster())

    await _settle_one(jobs, docker, record, job)

    assert job.status is JobStatus.FAILED
    assert job.error_code == "stack.update_failed"


async def test_a_container_that_vanished_is_not_guessed_at(
    db: AsyncSession,
) -> None:
    """Neither outcome may be invented here.

    Reporting success would claim an installation was updated when it may not
    have been; the honest answer is that nobody can tell any more, and that
    reads as a failure so somebody looks.
    """
    job, record = await _detached_update(db)
    docker: Any = VanishedUpdater()
    jobs = JobService(db, EventBroadcaster())

    await _settle_one(jobs, docker, record, job)

    assert job.status is JobStatus.FAILED
    assert "unknown" in (job.error_details or "")
