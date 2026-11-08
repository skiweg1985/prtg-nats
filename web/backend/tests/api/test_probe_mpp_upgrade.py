"""Upgrading the probe package over the management channel.

The enrollment installs whatever version the Paessler repository offers that
day and never looks at the package again. This is the way a fleet set up over
months catches up - announced in the list, started on purpose, checked after.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.enums import JobStatus
from app.domain.models import newer_package_version
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import CURRENT_HELPER_VERSION, ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.services.probes import ProbeService
from app.workers.job_runner import JobRunner
from tests.conftest import SETUP_CODE, ScriptedTransport, write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"


def probe_info(
    package: str,
    *,
    available: str | None = None,
    service: str = "active",
    helper: int = CURRENT_HELPER_VERSION,
    platform: str = "linux-arm64-glibc",
) -> str:
    return (
        "OK probe-info\n"
        f"package={package}\n"
        f"package_available={available or package}\n"
        f"service={service}\n"
        f"helper_version={helper}\n"
        f"platform={platform}\n"
        "config=/etc/paessler/mpprobe/config.yaml\n"
    )


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
    for _ in range(rounds):
        if not await runner._claim_and_run():
            return


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup",
        json={"setup_code": SETUP_CODE, "username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text


async def observe(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
    username: str,
) -> None:
    async with session_factory() as db:
        service = ProbeService(
            db,
            settings,
            RuntimeFileStore(settings),
            ProbeHelperClient(transport),
            SensorCatalog(settings.sensor_source_dir),
        )
        await service.refresh_observed_state(username)
        await db.commit()


async def upgrade(
    client: AsyncClient, settings: Settings, transport: ScriptedTransport
) -> str:
    listing = await client.get("/api/v1/probes")
    probe_id = listing.json()[0]["id"]
    accepted = await client.post(
        "/api/v1/probes/actions/mpp-upgrade", json={"probe_ids": [probe_id]}
    )
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))
    return str(accepted.json()["job_id"])


def test_only_a_newer_version_of_the_same_kind_counts() -> None:
    known = ["3.10.0-1", "3.11.0-1", "3.12.0", None]
    assert newer_package_version("3.10.0-1", known) == "3.11.0-1"
    assert newer_package_version("3.11.0-1", known) is None
    # Numbers, not text: 3.9 is older than 3.10.
    assert newer_package_version("3.9.1-1", ["3.10.0-1"]) == "3.10.0-1"
    # An RPM version never makes a Debian probe look outdated, nor the reverse.
    assert newer_package_version("3.10.0", known) == "3.12.0"
    assert newer_package_version(None, known) is None
    # dpkg's order: a pre-release before its release, an epoch above all.
    assert newer_package_version("3.11.0-1", ["3.11.0~rc1-1"]) is None
    assert newer_package_version("3.11.0~rc1-1", ["3.11.0-1"]) == "3.11.0-1"
    assert newer_package_version("3.11.0-1", ["1:3.10.0-1"]) == "1:3.10.0-1"
    assert newer_package_version("3.11.0-1+deb13", ["3.11.0-1+deb12"]) is None


async def test_the_list_names_a_release_another_probe_has_seen(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A probe with a stale package cache still learns about the release."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-hamburg-01")
    await sign_in(client)
    transport.responses["probe-info"] = probe_info("3.10.0-1")
    await observe(settings, session_factory, transport, "mpp-berlin-01")
    transport.responses["probe-info"] = probe_info("3.11.0-1")
    await observe(settings, session_factory, transport, "mpp-hamburg-01")

    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    rows = {row["nats_username"]: row for row in listing.json()}
    assert rows["mpp-berlin-01"]["package_update"] == "3.11.0-1"
    assert rows["mpp-hamburg-01"]["package_update"] is None

    detail = await client.get(f"/api/v1/probes/{rows['mpp-berlin-01']['id']}")
    assert detail.json()["summary"]["package_update"] == "3.11.0-1"


async def test_a_release_for_another_platform_is_not_offered(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The arm64 build can trail the amd64 one; offering it would end in
    "already current" on every run."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-hamburg-01")
    await sign_in(client)
    transport.responses["probe-info"] = probe_info("3.10.0-1")
    await observe(settings, session_factory, transport, "mpp-berlin-01")
    transport.responses["probe-info"] = probe_info(
        "3.11.0-1", platform="linux-amd64-glibc"
    )
    await observe(settings, session_factory, transport, "mpp-hamburg-01")

    rows = {
        row["nats_username"]: row for row in (await client.get("/api/v1/probes")).json()
    }
    assert rows["mpp-berlin-01"]["package_update"] is None
    detail = await client.get(f"/api/v1/probes/{rows['mpp-berlin-01']['id']}")
    assert detail.json()["summary"]["package_update"] is None


async def test_the_upgrade_runs_and_the_new_version_is_recorded(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    transport.responses["probe-info"] = [
        probe_info("3.10.0-1", available="3.11.0-1"),
        *[probe_info("3.11.0-1")] * 4,
    ]
    transport.responses["mpp-upgrade"] = (
        "OK mpp-upgraded from=3.10.0-1 to=3.11.0-1 state=upgraded service=active was=active\n"
    )

    job_id = await upgrade(client, settings, transport)

    # A current helper is not sent again.
    assert "helper-update" not in transport.commands()
    assert transport.commands()[:3] == ["probe-info", "mpp-upgrade", "probe-info"]
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL, job.error_code
        assert [step.name for step in job.steps] == [
            "check_reachable",
            "ensure_helper",
            "upgrade",
            "verify",
        ]
    row = (await client.get("/api/v1/probes")).json()[0]
    assert row["package_version"] == "3.11.0-1"
    assert row["package_update"] is None


async def test_an_older_helper_is_renewed_before_the_upgrade(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """mpp-upgrade arrived with helper 14; an older one would refuse it."""
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    transport.responses["probe-info"] = [
        probe_info("3.10.0-1", helper=CURRENT_HELPER_VERSION - 1),
        *[probe_info("3.11.0-1")] * 4,
    ]
    transport.responses["helper-update"] = (
        f"OK helper-updated version={CURRENT_HELPER_VERSION} sha256=bb\n"
    )
    transport.responses["mpp-upgrade"] = (
        "OK mpp-upgraded from=3.10.0-1 to=3.11.0-1 state=upgraded service=active was=active\n"
    )

    await upgrade(client, settings, transport)

    commands = transport.commands()
    assert commands.index("helper-update") < commands.index("mpp-upgrade")


async def test_a_service_that_does_not_come_back_fails_the_job(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    transport.responses["probe-info"] = [
        probe_info("3.10.0-1"),
        *[probe_info("3.11.0-1", service="inactive")] * 4,
    ]
    transport.responses["mpp-upgrade"] = (
        "OK mpp-upgraded from=3.10.0-1 to=3.11.0-1 state=upgraded service=inactive was=active\n"
    )

    job_id = await upgrade(client, settings, transport)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED


async def test_a_probe_on_the_newest_version_is_not_a_failure(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    transport.responses["probe-info"] = probe_info("3.11.0-1")
    transport.responses["mpp-upgrade"] = (
        "OK mpp-upgraded from=3.11.0-1 to=3.11.0-1 state=current service=active was=active\n"
    )

    job_id = await upgrade(client, settings, transport)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL
