"""A lease is there to survive a dead process, not to time-limit the work.

``LOCK_LEASE`` is thirty minutes, and a rollout across several probes outlives
that without anything being wrong: sensor-activate alone is allowed ten
minutes per probe, mpp-uninstall fifteen. The reaper used to free those locks
anyway - it only looked at the clock - and the next job took the probe while
the first one still had an SSH transaction open on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.jobs import ResourceLock
from app.services.events import EventBroadcaster
from app.services.jobs import LOCK_LEASE, JobRequest, JobService, ResourceRef


async def _job_holding(db: AsyncSession, probe: str) -> tuple[str, ResourceLock]:
    """A job with an expired lock on one probe - the reaper's whole input."""
    jobs = JobService(db, EventBroadcaster())
    job = await jobs.create(
        JobRequest(
            type="sensor.deploy",
            steps=("activate",),
            resources=(ResourceRef("probe", probe),),
        )
    )
    now = datetime.now(UTC)
    lock = ResourceLock(
        resource_type="probe",
        resource_id=probe,
        job_id=job.id,
        acquired_at=now - timedelta(hours=2),
        expires_at=now - timedelta(minutes=1),
    )
    db.add(lock)
    await db.flush()
    return job.id, lock


async def test_the_reaper_leaves_a_lock_its_worker_still_holds(
    db: AsyncSession,
) -> None:
    carried, _ = await _job_holding(db, "probe-a")
    await _job_holding(db, "probe-b")
    jobs = JobService(db, EventBroadcaster())

    released = await jobs.reap_expired_locks(keep={carried})

    remaining = list(await db.scalars(select(ResourceLock)))
    assert released == 1
    assert [lock.job_id for lock in remaining] == [carried]


async def test_renewing_pushes_the_lease_past_now(db: AsyncSession) -> None:
    carried, lock = await _job_holding(db, "probe-a")
    jobs = JobService(db, EventBroadcaster())

    renewed = await jobs.renew_locks({carried})

    await db.refresh(lock)
    assert renewed == 1
    assert lock.expires_at > datetime.now(UTC)
    assert lock.expires_at <= datetime.now(UTC) + LOCK_LEASE


async def test_renewing_nothing_touches_nothing(db: AsyncSession) -> None:
    """The idle reaper pass, which is most of them."""
    _, lock = await _job_holding(db, "probe-b")
    before = lock.expires_at
    jobs = JobService(db, EventBroadcaster())

    assert await jobs.renew_locks(set()) == 0

    await db.refresh(lock)
    assert lock.expires_at == before


async def test_reaping_without_a_carried_job_still_frees_everything(
    db: AsyncSession,
) -> None:
    """No worker of this process is carrying anything - the restart case."""
    await _job_holding(db, "probe-b")
    jobs = JobService(db, EventBroadcaster())

    assert await jobs.reap_expired_locks() == 1
    assert list(await db.scalars(select(ResourceLock))) == []
