"""The job engine: locks, live log, retry, and a sensor rollout end to end.

The probe is a ScriptedTransport rather than a real host, so the whole path -
API, job, lock, helper protocol, deployment record - runs in-process and fails
loudly when any link in it changes.
"""

from __future__ import annotations

import asyncio

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeRejectedError, ProbeUnreachableError
from app.domain.enums import JobStatus, JobStepStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import Deployment
from app.persistence.models.jobs import Job, JobEvent, ResourceLock
from app.services.events import get_broadcaster
from app.services.jobs import JobRequest, JobService, ResourceRef
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor

PASSWORD = "correct-horse-battery"


def build_runner(settings: Settings, transport: ScriptedTransport) -> JobRunner:
    return JobRunner(
        settings=settings,
        broadcaster=get_broadcaster(),
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        docker=DockerAdapter(settings.docker_socket),
    )


async def drain(runner: JobRunner, *, rounds: int = 12) -> None:
    """Run queued work to completion without starting the polling loop."""
    for _ in range(rounds):
        if not await runner._claim_and_run():
            await asyncio.sleep(0)


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


# --- Locking ----------------------------------------------------------------


async def test_two_jobs_on_the_same_probe_do_not_run_together(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The second waits and says so, rather than racing the first."""
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        first = await jobs.create(
            JobRequest(
                type="sensor.deploy",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        second = await jobs.create(
            JobRequest(
                type="sensor.deploy",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )

        assert await jobs.try_acquire_locks(first) is None
        blocking = await jobs.try_acquire_locks(second)
        assert blocking == first.id

        await jobs.release_to_queue(second, blocking)
        assert second.status is JobStatus.QUEUED
        assert second.started_at is None
        assert second.blocked_reason_key == "jobs.blocked.resource_busy"
        await db.commit()


async def test_releasing_a_lock_lets_the_next_job_take_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        first = await jobs.create(
            JobRequest(
                type="probe.validate",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        second = await jobs.create(
            JobRequest(
                type="probe.validate",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        await jobs.try_acquire_locks(first)
        await jobs.release_locks(first.id)

        assert await jobs.try_acquire_locks(second) is None
        await db.commit()


async def test_a_job_takes_all_its_resources_or_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A partial hold would let two jobs each own half of what they need."""
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        holder = await jobs.create(
            JobRequest(
                type="system.restart",
                steps=("a",),
                resources=(ResourceRef("nats", "server"),),
            )
        )
        await jobs.try_acquire_locks(holder)

        contender = await jobs.create(
            JobRequest(
                type="certificate.renew",
                steps=("a",),
                resources=(
                    ResourceRef("certificate", "server"),
                    ResourceRef("nats", "server"),
                ),
            )
        )
        assert await jobs.try_acquire_locks(contender) == holder.id

        held = list(await db.scalars(select(ResourceLock)))
        assert [lock.job_id for lock in held] == [holder.id]
        await db.commit()


async def test_expired_locks_are_reaped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A crashed worker must not own a probe for the rest of the day."""
    from datetime import UTC, datetime, timedelta

    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(
            JobRequest(
                type="probe.validate",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        await jobs.try_acquire_locks(job)

        lock = await db.scalar(select(ResourceLock))
        assert lock is not None
        lock.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.flush()

        assert await jobs.reap_expired_locks() == 1
        await db.commit()


# --- A rollout, end to end --------------------------------------------------


async def test_a_sensor_rollout_drives_the_helper_transaction(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed", version="2")
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=2.1.0\nservice=active\nca_sha256=aa\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    created = await client.post(
        "/api/v1/deployments",
        json={"sensor": "internet-speed", "probe_ids": [probe_id]},
    )
    assert created.status_code == 202, created.text
    deployment_id = created.json()["id"]

    await drain(build_runner(settings, transport))

    # stage -> activate -> commit, in that order, is the probe's own contract.
    commands = transport.commands()
    assert commands == [
        "probe-info",
        "sensor-prepare",
        "sensor-stage",
        "sensor-stage",
        "sensor-activate",
        "sensor-commit",
    ]

    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.SUCCESSFUL
        assert deployment.targets[0].status is JobStatus.SUCCESSFUL

        job = await db.get(Job, deployment.job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL
        assert all(step.status is JobStepStatus.SUCCEEDED for step in job.steps)


async def test_the_version_file_is_written_last(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """sensor-list reports the version, so it must not appear before the files
    it describes are in place."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed", version="2")
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    await client.post(
        "/api/v1/deployments",
        json={"sensor": "internet-speed", "probe_ids": [probe_id]},
    )
    await drain(build_runner(settings, transport))

    slots = [
        request.arguments[2]
        for _, request in transport.calls
        if request.command.value == "sensor-stage"
    ]
    assert slots == ["script", "version"]


async def test_a_dry_run_touches_nothing(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed")
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    await client.post(
        "/api/v1/deployments",
        json={"sensor": "internet-speed", "probe_ids": [probe_id], "dry_run": True},
    )
    await drain(build_runner(settings, transport))

    assert transport.commands() == ["probe-info"]


async def test_a_failed_activation_rolls_back_and_reports(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """The probe restores itself; the job has to say what happened and where."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed")
    transport.responses["sensor-activate"] = ProbeRejectedError(
        params={"probe": "mpp-berlin-01"},
        details="self-test produced no valid Script v2 JSON",
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    deployment_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": [probe_id]},
        )
    ).json()["id"]

    await drain(build_runner(settings, transport))

    assert "sensor-rollback" in transport.commands()

    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.FAILED
        target = deployment.targets[0]
        assert target.error_code == "probe.request_rejected"
        # The probe's own words, kept verbatim and untranslated.
        assert "Script v2" in (target.error_details or "")


async def test_one_unreachable_probe_does_not_stop_the_others(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01", host="berlin.example.test")
    write_probe_inventory(project_dir, "mpp-hamburg-01", host="hamburg.example.test")
    write_sensor(project_dir, "internet-speed")

    class PartiallyDown(ScriptedTransport):
        async def run(self, connection, request, timeout):  # type: ignore[no-untyped-def]
            if connection.nats_username == "mpp-hamburg-01":
                raise ProbeUnreachableError.of(
                    connection.nats_username, details="connection timed out"
                )
            return await super().run(connection, request, timeout)

    transport = PartiallyDown()
    await sign_in(client)

    probe_ids = [entry["id"] for entry in (await client.get("/api/v1/probes")).json()]
    deployment_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": probe_ids},
        )
    ).json()["id"]

    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.PARTIALLY_SUCCESSFUL

        outcomes = {target.probe_label: target.status for target in deployment.targets}
        assert outcomes["mpp-berlin-01"] is JobStatus.SUCCESSFUL
        assert outcomes["mpp-hamburg-01"] is JobStatus.FAILED

        job = await db.get(Job, deployment.job_id)
        assert job is not None
        assert job.status is JobStatus.PARTIALLY_SUCCESSFUL


# --- Log and retry ----------------------------------------------------------


async def test_the_log_is_structured_and_translatable(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """Every line carries a code and parameters; the browser writes the sentence."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed", version="2")
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    job_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": [probe_id]},
        )
    ).json()["job_id"]

    await drain(build_runner(settings, transport))

    log = await client.get(f"/api/v1/jobs/{job_id}/log")
    assert log.status_code == 200
    entries = log.json()

    assert entries, "a rollout must leave a log"
    assert all(entry["code"] for entry in entries)
    assert [entry["sequence"] for entry in entries] == sorted(
        entry["sequence"] for entry in entries
    )
    codes = {entry["code"] for entry in entries}
    assert "jobs.started" in codes
    assert "jobs.sensor.committed" in codes

    async with session_factory() as db:
        events = list(await db.scalars(select(JobEvent)))
    # No sentence is stored - only keys and parameters.
    assert all(" " not in event.code for event in events)


async def test_a_retry_is_a_new_job_that_remembers_the_old_one(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed")
    transport.responses["sensor-activate"] = ProbeRejectedError(details="boom")
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    job_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": [probe_id]},
        )
    ).json()["job_id"]
    await drain(build_runner(settings, transport))

    failed = await client.get(f"/api/v1/jobs/{job_id}")
    assert failed.json()["status"] == "failed"

    # Fix the probe, then retry.
    transport.responses.pop("sensor-activate")
    retried = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["job_id"] != job_id

    await drain(build_runner(settings, transport))

    detail = await client.get(f"/api/v1/jobs/{retried.json()['job_id']}")
    assert detail.json()["status"] == "successful"
    assert detail.json()["retry_of_job_id"] == job_id

    # The failed run stays in the history; that is what makes a pattern visible.
    assert (await client.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "failed"


async def test_a_running_job_cannot_be_retried(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await sign_in(client)
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(JobRequest(type="probe.validate", steps=("a",)))
        await jobs.mark_running(job)
        await db.commit()
        job_id = job.id

    response = await client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 409


async def test_cancelling_a_queued_job_stops_it_immediately(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await sign_in(client)
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(JobRequest(type="probe.validate", steps=("a",)))
        await db.commit()
        job_id = job.id

    response = await client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_an_unknown_job_type_fails_the_job_rather_than_the_worker(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
) -> None:
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(JobRequest(type="does.not.exist", steps=("a",)))
        await db.commit()
        job_id = job.id

    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        stored = await db.get(Job, job_id)
        assert stored is not None
        assert stored.status is JobStatus.FAILED
        assert stored.error_code == "jobs.unknown_type"


async def test_a_job_payload_never_stores_a_secret(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(
            JobRequest(
                type="probe.validate",
                steps=("a",),
                payload={"probe": "mpp-berlin-01", "password": "hunter2"},
            )
        )
        await db.commit()

    assert job.payload["probe"] == "mpp-berlin-01"
    assert job.payload["password"] != "hunter2"


async def test_a_job_can_only_be_claimed_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two workers polling the same table must not both run the same job.

    The regression this guards: a SELECT followed by an UPDATE let both
    workers see the row as queued, and a sensor rollout ran twice.
    """
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        await jobs.create(JobRequest(type="probe.validate", steps=("a",)))
        await db.commit()

    async with session_factory() as first_db, session_factory() as second_db:
        first = await JobService(first_db, get_broadcaster()).claim_next_queued()
        assert first is not None
        await first_db.commit()

        second = await JobService(second_db, get_broadcaster()).claim_next_queued()
        assert second is None

    async with session_factory() as db:
        stored = await db.get(Job, first.id)
        assert stored is not None
        assert stored.status is JobStatus.RUNNING
        assert stored.started_at is not None


async def test_a_failed_rollout_reports_a_code_and_marks_the_step(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """A failed job with no error code renders as a failure with no explanation."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed")
    transport.responses["probe-info"] = ProbeUnreachableError.of(
        "mpp-berlin-01", details="connection timed out"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    job_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": [probe_id]},
        )
    ).json()["job_id"]
    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error_code == "jobs.all_targets_failed"
        # The steps after the failure must not read as succeeded.
        assert not all(step.status is JobStepStatus.SUCCEEDED for step in job.steps)
