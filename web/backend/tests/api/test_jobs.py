"""The job engine: locks, live log, retry, and a sensor rollout end to end.

The probe is a ScriptedTransport rather than a real host, so the whole path -
API, job, lock, helper protocol, deployment record - runs in-process and fails
loudly when any link in it changes.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.routes import jobs as jobs_route
from app.core.config import Settings
from app.core.errors import (
    ProbeProtocolError,
    ProbeRejectedError,
    ProbeUnreachableError,
)
from app.domain.enums import JobStatus, JobStepStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import Deployment
from app.persistence.models.jobs import Job, JobEvent, ResourceLock
from app.services.events import StreamEvent, get_broadcaster, job_topic
from app.services.jobs import JobRequest, JobService, ResourceRef
from app.workers.job_runner import JobRunner
from tests.conftest import (
    ScriptedTransport,
    write_probe_inventory,
    write_sensor,
    write_tool_artifact,
)

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
        "OK probe-info\npackage=2.1.0\nservice=active\nca_sha256=aa\nhelper_version=8\n"
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
    # The probe-info/sensor-list pair after it is the runner asking the probe
    # how the job left it, so the sensor does not read as missing until the
    # cached state expires.
    commands = transport.commands()
    assert commands == [
        "probe-info",
        "sensor-prepare",
        "sensor-stage",
        "sensor-stage",
        "sensor-activate",
        "sensor-commit",
        "probe-info",
        "sensor-list",
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


async def test_a_managed_tool_is_selected_and_staged_before_sensor_files(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    platform = "linux-arm64-glibc"
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(
        project_dir,
        "iperf-throughput",
        version="2",
        managed_tool="iperf3",
    )
    write_tool_artifact(project_dir, "iperf3", platform, b"approved executable")
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=2.1.0\nservice=active\n"
        f"helper_version=8\nplatform={platform}\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    await client.post(
        "/api/v1/deployments",
        json={"sensor": "iperf-throughput", "probe_ids": [probe_id]},
    )
    await drain(build_runner(settings, transport))

    commands = transport.commands()
    assert commands.index("sensor-tool-stage") < commands.index("sensor-stage")
    request = next(
        request
        for _, request in transport.calls
        if request.command.value == "sensor-tool-stage"
    )
    assert len(request.arguments) == 3
    assert request.arguments[1] == "iperf-throughput"
    assert request.payload is not None
    assert f"platform={platform}\n" in request.payload
    assert "version=3.21\n" in request.payload


async def test_an_ambiguous_sensor_commit_is_confirmed_idempotently(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "dns-check")
    transport.responses["sensor-commit"] = [
        ProbeUnreachableError.of("mpp-berlin-01"),
        "OK sensor-committed dns-check\n",
    ]
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    accepted = await client.post(
        "/api/v1/deployments",
        json={"sensor": "dns-check", "probe_ids": [probe_id]},
    )
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))

    assert transport.commands().count("sensor-commit") == 2
    assert "sensor-rollback" not in transport.commands()


@pytest.mark.parametrize(
    "ambiguous_error",
    [
        ProbeRejectedError(details="the commit answer could not be classified"),
        ProbeProtocolError(details="the commit answer was incomplete"),
    ],
    ids=["rejected", "protocol-error"],
)
async def test_every_ambiguous_commit_error_is_confirmed_once(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
    ambiguous_error: Exception,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "dns-check")
    transport.responses["sensor-commit"] = [
        ambiguous_error,
        "OK sensor-committed dns-check\n",
    ]
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    accepted = await client.post(
        "/api/v1/deployments",
        json={"sensor": "dns-check", "probe_ids": [probe_id]},
    )
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))

    assert transport.commands().count("sensor-commit") == 2
    assert "sensor-rollback" not in transport.commands()


async def test_a_regular_sensor_updates_an_old_helper_before_commit_retry(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "dns-check")
    transport.responses["probe-info"] = [
        "OK probe-info\npackage=2.1.0\nhelper_version=7\n",
        "OK probe-info\npackage=2.1.0\nhelper_version=8\n",
        "OK probe-info\npackage=2.1.0\nhelper_version=8\n",
    ]
    transport.responses["sensor-commit"] = [
        ProbeUnreachableError.of("mpp-berlin-01"),
        "OK sensor-committed dns-check\n",
    ]
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    accepted = await client.post(
        "/api/v1/deployments",
        json={"sensor": "dns-check", "probe_ids": [probe_id]},
    )
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))

    commands = transport.commands()
    assert commands[:4] == [
        "probe-info",
        "helper-update",
        "probe-info",
        "sensor-prepare",
    ]
    assert commands.count("sensor-commit") == 2
    assert "sensor-rollback" not in commands
    log = await client.get(f"/api/v1/jobs/{accepted.json()['job_id']}/log")
    codes = [entry["code"] for entry in log.json()]
    assert "jobs.probe.helper_sent" in codes
    assert "jobs.probe.helper_updated" in codes


async def test_a_managed_tool_rollout_updates_an_old_helper_first(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    platform = "linux-arm64-glibc"
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(
        project_dir,
        "iperf-throughput",
        version="2",
        managed_tool="iperf3",
    )
    write_tool_artifact(project_dir, "iperf3", platform, b"approved executable")
    transport.responses["probe-info"] = [
        "OK probe-info\npackage=2.1.0\nhelper_version=6\n",
        f"OK probe-info\npackage=2.1.0\nhelper_version=8\nplatform={platform}\n",
        f"OK probe-info\npackage=2.1.0\nhelper_version=8\nplatform={platform}\n",
    ]
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    await client.post(
        "/api/v1/deployments",
        json={"sensor": "iperf-throughput", "probe_ids": [probe_id]},
    )
    await drain(build_runner(settings, transport))

    commands = transport.commands()
    assert commands[:4] == [
        "probe-info",
        "helper-update",
        "probe-info",
        "sensor-prepare",
    ]
    assert commands.index("helper-update") < commands.index("sensor-tool-stage")


async def test_a_missing_platform_artifact_fails_before_sensor_staging(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(
        project_dir,
        "iperf-throughput",
        version="2",
        managed_tool="iperf3",
    )
    write_tool_artifact(
        project_dir, "iperf3", "linux-arm64-glibc", b"approved executable"
    )
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=2.1.0\nservice=active\n"
        "helper_version=8\nplatform=linux-amd64-glibc\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    deployment_id = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "iperf-throughput", "probe_ids": [probe_id]},
        )
    ).json()["id"]
    await drain(build_runner(settings, transport))

    assert "sensor-prepare" not in transport.commands()
    assert "sensor-stage" not in transport.commands()
    assert "sensor-tool-stage" not in transport.commands()
    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.FAILED


async def test_dry_run_accepts_system_fallback_on_an_unmanaged_platform(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "iperf-throughput", managed_tool="iperf3")
    write_tool_artifact(
        project_dir, "iperf3", "linux-arm64-glibc", b"approved executable"
    )
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=2.1.0\nhelper_version=8\nplatform=linux-riscv64-glibc\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    deployment_id = (
        await client.post(
            "/api/v1/deployments",
            json={
                "sensor": "iperf-throughput",
                "probe_ids": [probe_id],
                "dry_run": True,
            },
        )
    ).json()["id"]
    await drain(build_runner(settings, transport))

    assert "helper-update" not in transport.commands()
    assert "sensor-prepare" not in transport.commands()
    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.SUCCESSFUL


async def test_system_fallback_activates_without_staging_managed_bytes(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    platform = "linux-riscv64-glibc"
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "iperf-throughput", managed_tool="iperf3")
    # The release manifest remains present and valid; this platform is simply
    # outside the exact managed matrix and is validated on the probe instead.
    write_tool_artifact(
        project_dir, "iperf3", "linux-arm64-glibc", b"approved executable"
    )
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=2.1.0\nservice=active\n"
        f"helper_version=8\nplatform={platform}\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    await client.post(
        "/api/v1/deployments",
        json={"sensor": "iperf-throughput", "probe_ids": [probe_id]},
    )
    await drain(build_runner(settings, transport))

    commands = transport.commands()
    assert "sensor-tool-stage" not in commands
    assert commands.index("sensor-stage") < commands.index("sensor-activate")
    assert commands.index("sensor-activate") < commands.index("sensor-commit")


async def test_dry_run_rejects_a_tampered_managed_tool_artifact(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    platform = "linux-arm64-glibc"
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "iperf-throughput", managed_tool="iperf3")
    artifact = write_tool_artifact(
        project_dir, "iperf3", platform, b"approved executable"
    )
    artifact.write_bytes(b"tampered")
    transport.responses["probe-info"] = (
        f"OK probe-info\npackage=2.1.0\nhelper_version=8\nplatform={platform}\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    deployment_id = (
        await client.post(
            "/api/v1/deployments",
            json={
                "sensor": "iperf-throughput",
                "probe_ids": [probe_id],
                "dry_run": True,
            },
        )
    ).json()["id"]
    await drain(build_runner(settings, transport))

    assert "sensor-prepare" not in transport.commands()
    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        assert deployment.status is JobStatus.FAILED


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

    # The pair at the end reads the probe, it does not change it - the point of
    # a dry run is that nothing is prepared, staged, activated or committed.
    assert transport.commands() == ["probe-info", "probe-info", "sensor-list"]


async def test_configuring_a_probe_without_the_package_stages_nothing(
    client: AsyncClient,
    settings: Settings,
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """The same guard the enrolment has, on the path everything shares.

    Enrolment is not the only way here - configure, reconcile and a credential
    rotation all run this transaction, and none of them can succeed while the
    probe has no prtg.mpprobe.service to restart. Refused before the first
    file is staged, so there is nothing to roll back.
    """
    write_probe_inventory(project_dir, "mpp-berlin-01")
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=none\nservice=inactive\n"
    )
    await sign_in(client)

    probe_id = (await client.get("/api/v1/probes")).json()[0]["id"]
    accepted = await client.post(
        "/api/v1/probes/actions/configure", json={"probe_ids": [probe_id]}
    )
    job_id = accepted.json()["job_id"]

    await drain(build_runner(settings, transport))

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "failed"
    assert job["error_code"] == "probe.package_missing"
    # Nothing staged. The read at the end is the runner refreshing the cached
    # state, which after a failure is worth more than after a success: what the
    # job left behind is exactly what nobody knows yet.
    assert transport.commands() == ["probe-info", "probe-info", "sensor-list"]


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
        details = target.error_details or ""
        assert "Script v2" in details
        assert "Sensor: internet-speed" in details
        transaction = next(
            line.removeprefix("Transaction: ")
            for line in details.splitlines()
            if line.startswith("Transaction: ")
        )
        assert (
            "./prtg-nats sensor recover internet-speed mpp-berlin-01 "
            f"--transaction {transaction}"
        ) in details


async def test_a_previous_active_transaction_is_the_reported_recovery_target(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    transport: ScriptedTransport,
) -> None:
    """A colliding deploy Y must point recovery at the transaction X it met."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_sensor(project_dir, "internet-speed")
    transport.responses["sensor-activate"] = ProbeRejectedError(
        params={
            "probe": "mpp-berlin-01",
            "command": "sensor-activate",
            "active_transaction": "tx-old",
        },
        details=(
            "ERROR: Sensor internet-speed has an active transaction\n"
            "active_transaction=tx-old"
        ),
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

    staged_transaction = next(
        request.arguments[0]
        for _, request in transport.calls
        if request.command.value == "sensor-stage"
    )
    rollback_transaction = next(
        request.arguments[0]
        for _, request in transport.calls
        if request.command.value == "sensor-rollback"
    )
    assert rollback_transaction == staged_transaction
    assert staged_transaction != "tx-old"

    async with session_factory() as db:
        deployment = await db.get(Deployment, deployment_id)
        assert deployment is not None
        details = deployment.targets[0].error_details or ""
    command = (
        "sudo ./prtg-nats sensor recover internet-speed mpp-berlin-01 "
        "--transaction tx-old"
    )
    assert command in details
    assert f"--transaction {staged_transaction}" not in details


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


async def test_a_blocked_job_is_skipped_rather_than_claimed_and_handed_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The regression this guards: a probe locked by a job that never returned
    left four workers claiming the job behind it and releasing it again, once
    a second each, until SQLite reported "database is locked" to everything
    else on the platform.
    """
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        holder = await jobs.create(
            JobRequest(
                type="probe.unenroll",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        waiting = await jobs.create(
            JobRequest(
                type="probe.configure",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        assert await jobs.try_acquire_locks(holder) is None
        holder.status = JobStatus.RUNNING
        await db.commit()

    async with session_factory() as db:
        assert await JobService(db, get_broadcaster()).claim_next_queued() is None
        await db.commit()

    async with session_factory() as db:
        stored = await db.get(Job, waiting.id)
        assert stored is not None
        assert stored.status is JobStatus.QUEUED
        assert stored.started_at is None
        assert stored.blocked_by_job_id == holder.id


async def test_a_blocked_job_does_not_hold_up_the_queue_behind_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        holder = await jobs.create(
            JobRequest(
                type="probe.unenroll",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        await jobs.create(
            JobRequest(
                type="probe.configure",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        elsewhere = await jobs.create(
            JobRequest(
                type="probe.validate",
                steps=("a",),
                resources=(ResourceRef("probe", "probe-2"),),
            )
        )
        assert await jobs.try_acquire_locks(holder) is None
        holder.status = JobStatus.RUNNING
        await db.commit()

    async with session_factory() as db:
        claimed = await JobService(db, get_broadcaster()).claim_next_queued()
        assert claimed is not None
        assert claimed.id == elsewhere.id
        await db.commit()


async def test_a_job_left_running_by_a_dead_process_is_cleared_at_startup(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
) -> None:
    """A restart mid-job used to leave the row running for ever.

    Nothing was carrying it, so the cancel button set a flag no one read, and
    the lock it held kept every later job on that probe queued behind it.
    """
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        abandoned = await jobs.create(
            JobRequest(
                type="probe.unenroll",
                steps=("remove_sensors", "revoke_access"),
                resources=(ResourceRef("probe", "probe-1"),),
            )
        )
        assert await jobs.try_acquire_locks(abandoned) is None
        abandoned.status = JobStatus.RUNNING
        abandoned.started_at = datetime.now(UTC)
        abandoned.cancel_requested = True
        await db.commit()

    runner = build_runner(settings, transport)
    await runner.start()
    await runner.stop()

    async with session_factory() as db:
        stored = await db.get(Job, abandoned.id)
        assert stored is not None
        # Cancelled, not failed: the operator asked for this, and a red row
        # would misreport what happened.
        assert stored.status is JobStatus.CANCELLED
        assert stored.finished_at is not None
        assert [step.status for step in stored.steps] == [
            JobStepStatus.SKIPPED,
            JobStepStatus.SKIPPED,
        ]
        held = list(
            await db.scalars(
                select(ResourceLock).where(ResourceLock.job_id == abandoned.id)
            )
        )
        assert held == []


async def test_a_running_job_this_process_carries_survives_the_reaper(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
) -> None:
    """The reaper decides by asking the runner, so it must not touch its own."""
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        mine = await jobs.create(JobRequest(type="probe.validate", steps=("a",)))
        theirs = await jobs.create(JobRequest(type="probe.validate", steps=("a",)))
        for job in (mine, theirs):
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()

    runner = build_runner(settings, transport)
    runner._active.add(mine.id)
    async with session_factory() as db:
        ended = await runner._end_abandoned_jobs(
            db, JobService(db, get_broadcaster()), grace=timedelta(seconds=90)
        )
        await db.commit()
    assert ended == 1

    async with session_factory() as db:
        assert (await db.get(Job, mine.id)).status is JobStatus.RUNNING  # type: ignore[union-attr]
        assert (await db.get(Job, theirs.id)).status is JobStatus.FAILED  # type: ignore[union-attr]
        assert (await db.get(Job, theirs.id)).error_code == "jobs.orphaned"  # type: ignore[union-attr]


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


# --- The live stream --------------------------------------------------------


class OpenStream:
    """One event stream, driven over ASGI instead of through the test client.

    httpx's ASGITransport collects the whole response before it hands one back,
    which never happens for a stream that stays open - so a test that watches an
    event arrive has to speak ASGI itself.
    """

    def __init__(self, app: object, path: str, cookie: str) -> None:
        self._app = app
        self._path = path
        self._cookie = cookie
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.status: int | None = None
        self.received = b""

    async def __aenter__(self) -> OpenStream:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"cookie", self._cookie.encode()),
                (b"accept", b"text/event-stream"),
            ],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
        }

        async def receive() -> dict[str, object]:
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                await self._chunks.put(bytes(message.get("body", b"")))

        self._task = asyncio.create_task(self._app(scope, receive, send))  # type: ignore[operator]
        return self

    async def __aexit__(self, *_: object) -> None:
        self._disconnect.set()
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def read_until(self, marker: bytes, *, timeout: float = 5.0) -> None:
        """Collect chunks until the marker shows up, or fail the test."""
        while marker not in self.received:
            self.received += await asyncio.wait_for(self._chunks.get(), timeout=timeout)


async def open_stream(app: object, client: AsyncClient, job_id: str) -> OpenStream:
    cookie = f"prtg_nats_session={client.cookies['prtg_nats_session']}"
    return OpenStream(app, f"/api/v1/jobs/{job_id}/events", cookie)


async def queued_job(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as db:
        job = await JobService(db, get_broadcaster()).create(
            JobRequest(type="probe.validate", steps=("check",))
        )
        job_id: str = job.id
        await db.commit()
    return job_id


async def test_the_live_stream_survives_an_idle_keepalive(
    app: Any,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quiet interval must not take the subscription down with it.

    The keepalive used to cancel the wait for the next line, which ran the
    subscription's cleanup and closed it for good; the line after that ended the
    response with "async generator raised StopAsyncIteration", and the operator
    watched a job that had stopped logging.
    """
    monkeypatch.setattr(jobs_route, "SSE_KEEPALIVE_SECONDS", 0.05)
    await sign_in(client)
    job_id = await queued_job(session_factory)

    async with await open_stream(app, client, job_id) as stream:
        await stream.read_until(b": keepalive")
        assert stream.status == 200

        await get_broadcaster().publish(
            StreamEvent(
                topic=job_topic(job_id),
                kind="job.event",
                payload={"sequence": 1, "code": "jobs.started"},
            )
        )
        await stream.read_until(b"jobs.started")


async def test_an_open_stream_does_not_hold_the_sqlite_write_lock(
    app: Any,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite has one writer, and a stream lasts as long as the page is open.

    Authenticating the request slides the login session's last_seen_at forward,
    so a request session left open for the length of the response held the write
    lock for exactly that long: every worker's claim then failed with "database
    is locked" until the operator closed the tab.
    """
    monkeypatch.setattr(jobs_route, "SSE_KEEPALIVE_SECONDS", 0.05)
    await sign_in(client)
    job_id = await queued_job(session_factory)

    async with await open_stream(app, client, job_id) as stream:
        # Past the first keepalive the route body has finished and the stream is
        # all that is left of the request.
        await stream.read_until(b": keepalive")

        async with session_factory() as db:
            claimed = await JobService(db, get_broadcaster()).claim_next_queued()
            assert claimed is not None
            assert claimed.id == job_id
            # With the lock held this waits out the busy timeout and raises.
            await asyncio.wait_for(db.commit(), timeout=3)


async def test_a_line_written_while_the_backlog_is_read_still_arrives(
    app: Any,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window between the stored half and the live one has to be closed.

    The route used to read the backlog and subscribe afterwards, and a worker
    logging in between wrote into neither: the line was past the query and
    ahead of the subscription. Reproduced by publishing from inside the backlog
    read, which is exactly where the worker was.
    """
    monkeypatch.setattr(jobs_route, "SSE_KEEPALIVE_SECONDS", 0.05)
    await sign_in(client)
    job_id = await queued_job(session_factory)

    original = JobService.events
    published = False

    async def events_that_race(self: JobService, job_id_arg: str, **kwargs: Any) -> Any:
        nonlocal published
        result = await original(self, job_id_arg, **kwargs)
        if not published:
            published = True
            await get_broadcaster().publish(
                StreamEvent(
                    topic=job_topic(job_id_arg),
                    kind="job.event",
                    payload={"sequence": 1, "code": "jobs.in_the_window"},
                )
            )
        return result

    monkeypatch.setattr(JobService, "events", events_that_race)

    async with await open_stream(app, client, job_id) as stream:
        await asyncio.wait_for(stream.read_until(b"jobs.in_the_window"), 10)


async def test_a_replayed_line_is_not_sent_a_second_time(
    app: Any,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listening before reading means the two halves can overlap.

    Whatever the backlog already carried has to be dropped when it arrives
    again on the live side, or the operator reads the same line twice.
    """
    monkeypatch.setattr(jobs_route, "SSE_KEEPALIVE_SECONDS", 0.05)
    await sign_in(client)
    job_id = await queued_job(session_factory)

    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.get(job_id)
        await jobs.log(job, "jobs.stored_line")
        await db.commit()

    async with await open_stream(app, client, job_id) as stream:
        await asyncio.wait_for(stream.read_until(b"jobs.stored_line"), 10)

        # The same sequence the backlog already replayed.
        await get_broadcaster().publish(
            StreamEvent(
                topic=job_topic(job_id),
                kind="job.event",
                payload={"sequence": 1, "code": "jobs.stored_line"},
            )
        )
        await get_broadcaster().publish(
            StreamEvent(
                topic=job_topic(job_id),
                kind="job.event",
                payload={"sequence": 2, "code": "jobs.fresh_line"},
            )
        )
        await asyncio.wait_for(stream.read_until(b"jobs.fresh_line"), 10)

    assert stream.received.count(b"jobs.stored_line") == 1


async def test_the_whole_backlog_is_replayed_not_the_first_page(
    app: Any,
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page size is not a reason to lose the middle of a job's log.

    One query with the service's own limit cut the replay off and the stream
    carried on from the live end, so everything between the page and the
    present was gone for good.
    """
    monkeypatch.setattr(jobs_route, "SSE_KEEPALIVE_SECONDS", 0.05)
    monkeypatch.setattr(jobs_route, "_BACKLOG_PAGE", 2)
    await sign_in(client)
    job_id = await queued_job(session_factory)

    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.get(job_id)
        for index in range(5):
            await jobs.log(job, f"jobs.line_{index}")
        await db.commit()

    async with await open_stream(app, client, job_id) as stream:
        await asyncio.wait_for(stream.read_until(b"jobs.line_4"), 10)

    for index in range(5):
        assert f"jobs.line_{index}".encode() in stream.received
