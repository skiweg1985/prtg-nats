"""Channel state, the silence warning and the repair job (issue #176).

What a probe delivers over its return channel is the only sign of life the
channel gives: the service on the probe posts when a sensor wrote something,
and an empty batch as a heartbeat every few minutes. The platform keeps the
last contact, the buffer fill level and the gaps the probe reported as
observed state (ADR 0002), warns when a probe falls silent, and repairs the
channel over the management access.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.probe.deps import get_probe_identity
from app.core.config import Settings
from app.domain.enums import JobStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.probe_helper import CURRENT_HELPER_VERSION, ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import (
    Alert,
    ProbeObservedState,
    ProbeReturnChannel,
)
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.workers.inventory_sync import InventorySync
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"
REPAIR = "probe.channel_repair"


def probe_info(
    *,
    channel_cert: str = "2099-01-31",
    channel_service: str = "active",
    issued_ago: timedelta | None = None,
) -> str:
    # What a helper from version 13 on adds: when the certificate in place
    # was issued, so a probe that never delivered can fall silent too.
    issued = (
        ""
        if issued_ago is None
        else "channel_cert_issued="
        + (datetime.now(UTC) - issued_ago).strftime("%Y-%m-%dT%H:%M:%SZ")
        + "\n"
    )
    return (
        "OK probe-info\n"
        "package=2.1.0\n"
        "service=active\n"
        f"helper_version={CURRENT_HELPER_VERSION}\n"
        "hostname=berlin-probe-01.example.test\n"
        "config=/etc/paessler/mpprobe/config.yaml\n"
        f"channel_cert={channel_cert}\n"
        f"channel_service={channel_service}\n"
        f"{issued}"
    )


def record(record_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": record_id,
        "sensor": "wlan-auth",
        "time": "2026-11-08T09:15:00Z",
        "result": "ok",
        "summary": "Authenticated",
        **extra,
    }


def gap(record_id: str, *, until: str = "2026-11-08T09:10:00Z") -> dict[str, Any]:
    return record(
        record_id,
        sensor="return-channel",
        result="gap",
        code="buffer_full",
        time=until,
        summary="3 record(s) dropped because the buffer on the probe was full",
        body={"dropped": 3, "from": "2026-11-08T09:00:00Z", "until": until},
    )


@pytest.fixture
def as_probe(app: Any) -> Iterator[None]:
    app.dependency_overrides[get_probe_identity] = lambda: PROBE
    yield
    app.dependency_overrides.pop(get_probe_identity, None)


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


async def probe_detail(client: AsyncClient) -> dict[str, Any]:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    detail = await client.get(f"/api/v1/probes/{listing.json()[0]['id']}")
    assert detail.status_code == 200, detail.text
    return dict(detail.json())


def build_sync(settings: Settings, transport: ScriptedTransport) -> InventorySync:
    return InventorySync(
        settings=settings,
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        nats=NatsMonitoringClient(settings.nats_monitoring_url),
        docker=DockerAdapter(settings.docker_socket),
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


async def last_contact_ago(
    session_factory: async_sessionmaker[AsyncSession], age: timedelta
) -> None:
    """Move the last delivery into the past, as if the probe had gone quiet."""
    async with session_factory() as db:
        await db.execute(
            update(ProbeReturnChannel).values(last_contact_at=datetime.now(UTC) - age)
        )
        await db.commit()


async def observed_state_due(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Have the next sync pass ask the probe again rather than wait."""
    async with session_factory() as db:
        await db.execute(update(ProbeObservedState).values(refresh_due=True))
        await db.commit()


async def repair_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[Job]:
    async with session_factory() as db:
        return list(await db.scalars(select(Job).where(Job.type == REPAIR)))


async def alert_kinds(session_factory: async_sessionmaker[AsyncSession]) -> set[str]:
    async with session_factory() as db:
        return {alert.kind for alert in await db.scalars(select(Alert))}


# --- Channel state -----------------------------------------------------------


async def test_a_delivery_updates_the_channel_state_on_the_probe_page(
    client: AsyncClient, project_dir: Path, as_probe: None
) -> None:
    write_probe_inventory(project_dir, PROBE)
    response = await client.post(
        "/probe/v1/records",
        json={
            "records": [
                record("11111111-0000-4000-8000-000000000001"),
                gap("11111111-0000-4000-8000-000000000002"),
            ],
            "buffer": {"bytes": 4096, "limit": 20971520},
        },
    )
    assert response.status_code == 200, response.text

    await sign_in(client)
    channel = (await probe_detail(client))["return_channel"]
    assert channel["buffer_bytes"] == 4096
    assert channel["buffer_limit"] == 20971520
    assert channel["gap_count"] == 1
    assert channel["last_gap_at"] == "2026-11-08T09:10:00Z"
    assert channel["silent"] is False
    contact = datetime.fromisoformat(channel["last_contact_at"])
    assert datetime.now(UTC) - contact < timedelta(minutes=1)


async def test_a_resent_gap_is_counted_once(
    client: AsyncClient, project_dir: Path, as_probe: None
) -> None:
    """A batch sent again after a lost answer must not double the gaps."""
    write_probe_inventory(project_dir, PROBE)
    batch = {"records": [gap("11111111-0000-4000-8000-000000000002")]}
    await client.post("/probe/v1/records", json=batch)
    await client.post("/probe/v1/records", json=batch)

    await sign_in(client)
    assert (await probe_detail(client))["return_channel"]["gap_count"] == 1


async def test_a_probe_without_a_channel_shows_none(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    assert (await probe_detail(client))["return_channel"] is None


# --- Silence -----------------------------------------------------------------


async def test_a_probe_quiet_past_the_threshold_is_silent_and_raises_an_alert(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info()
    await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000001")]},
    )
    sync = build_sync(settings, transport)

    await last_contact_ago(session_factory, timedelta(minutes=14))
    await sync.run_once()
    assert "probe.channel_silent" not in await alert_kinds(session_factory)

    await last_contact_ago(session_factory, timedelta(minutes=16))
    await sync.run_once()
    assert "probe.channel_silent" in await alert_kinds(session_factory)

    await sign_in(client)
    detail = await probe_detail(client)
    assert detail["return_channel"]["silent"] is True
    assert detail["return_channel"]["certificate_expires"] == "2099-01-31"
    assert detail["summary"]["channel_silent"] is True


async def test_an_empty_batch_is_a_heartbeat(
    client: AsyncClient, project_dir: Path, as_probe: None
) -> None:
    """The service sends one every few minutes with nothing in it, so the
    last contact moves without any sensor writing to the log."""
    write_probe_inventory(project_dir, PROBE)
    response = await client.post(
        "/probe/v1/records",
        json={"records": [], "buffer": {"bytes": 0, "limit": 20971520}},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"stored": [], "duplicates": []}

    await sign_in(client)
    channel = (await probe_detail(client))["return_channel"]
    assert channel["buffer_bytes"] == 0
    assert channel["buffer_limit"] == 20971520
    assert channel["gap_count"] == 0
    contact = datetime.fromisoformat(channel["last_contact_at"])
    assert datetime.now(UTC) - contact < timedelta(minutes=1)


async def test_a_probe_that_never_delivered_is_silent_once_its_certificate_is_old(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """With a heartbeat, a certificate and a running service that never
    produced a single delivery is a channel that does not work."""
    write_probe_inventory(project_dir, PROBE)
    sync = build_sync(settings, transport)

    transport.responses["probe-info"] = probe_info(issued_ago=timedelta(minutes=5))
    await sync.run_once()
    assert "probe.channel_silent" not in await alert_kinds(session_factory)
    assert await repair_jobs(session_factory) == []

    transport.responses["probe-info"] = probe_info(issued_ago=timedelta(minutes=16))
    await observed_state_due(session_factory)
    await sync.run_once()
    assert "probe.channel_silent" in await alert_kinds(session_factory)
    assert [job.trigger for job in await repair_jobs(session_factory)] == ["system"]
    await sign_in(client)
    channel = (await probe_detail(client))["return_channel"]
    assert channel["silent"] is True
    assert channel["last_contact_at"] is None


async def test_a_probe_whose_helper_does_not_say_when_it_was_issued_is_not_silent(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A helper before version 13 sends no heartbeat either; its probe may
    simply have no sensor that writes to the log."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info()
    await build_sync(settings, transport).run_once()

    assert "probe.channel_silent" not in await alert_kinds(session_factory)
    assert await repair_jobs(session_factory) == []
    await sign_in(client)
    channel = (await probe_detail(client))["return_channel"]
    assert channel["silent"] is False
    assert channel["service"] == "active"


async def test_an_expired_certificate_starts_a_repair(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    """Not waiting for the silence threshold: a probe that just delivered
    with a certificate that expires today will not deliver again."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info(channel_cert="2020-01-31")
    await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000001")]},
    )
    await build_sync(settings, transport).run_once()

    assert [job.trigger for job in await repair_jobs(session_factory)] == ["system"]
    await sign_in(client)
    detail = await probe_detail(client)
    assert detail["return_channel"]["certificate_expired"] is True
    assert detail["summary"]["channel_certificate_expired"] is True


# --- Repair --------------------------------------------------------------------


async def start_repair(client: AsyncClient) -> str:
    listing = await client.get("/api/v1/probes")
    probe_id = listing.json()[0]["id"]
    accepted = await client.post(f"/api/v1/probes/{probe_id}/return-channel/repair")
    assert accepted.status_code == 202, accepted.text
    return str(accepted.json()["job_id"])


async def finished(
    session_factory: async_sessionmaker[AsyncSession], job_id: str
) -> Job:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def test_a_silent_probe_is_repaired_and_delivers_again(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    """The service runs and the probe is still quiet, so reinstalling the
    service alone would change nothing: the repair issues a new certificate,
    which also rewrites and restarts the service."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info()
    await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000001")]},
    )
    await last_contact_ago(session_factory, timedelta(hours=1))
    await sign_in(client)

    job_id = await start_repair(client)
    await drain(build_runner(settings, transport))

    job = await finished(session_factory, job_id)
    assert job.status is JobStatus.SUCCESSFUL
    assert job.trigger == "user"
    commands = transport.commands()
    assert commands[:4] == [
        "probe-info",
        "channel-csr",
        "channel-install",
        "probe-info",
    ]

    # The probe catches up from its buffer, and the warning goes with it.
    response = await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000003")]},
    )
    assert response.status_code == 200, response.text
    await build_sync(settings, transport).run_once()
    assert "probe.channel_silent" not in await alert_kinds(session_factory)
    assert (await probe_detail(client))["return_channel"]["silent"] is False


async def test_a_repair_of_a_probe_that_still_delivers_keeps_its_certificate(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    """The service runs and the last heartbeat is recent: nothing points at
    the certificate, so only the service is refreshed."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info()
    await client.post("/probe/v1/records", json={"records": []})
    await sign_in(client)

    job_id = await start_repair(client)
    await drain(build_runner(settings, transport))

    assert (await finished(session_factory, job_id)).status is JobStatus.SUCCESSFUL
    commands = transport.commands()
    assert "channel-service" in commands
    assert "channel-csr" not in commands


async def test_a_stopped_service_is_reinstalled_without_a_new_certificate(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info(channel_service="inactive")
    await sign_in(client)

    job_id = await start_repair(client)
    await drain(build_runner(settings, transport))

    assert (await finished(session_factory, job_id)).status is JobStatus.SUCCESSFUL
    commands = transport.commands()
    assert "channel-service" in commands
    assert "channel-csr" not in commands


@pytest.mark.parametrize("channel_cert", ["none", "2020-01-31"])
async def test_a_missing_or_expired_certificate_is_issued_anew(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
    channel_cert: str,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info(
        channel_cert=channel_cert, channel_service="inactive"
    )
    await sign_in(client)

    job_id = await start_repair(client)
    await drain(build_runner(settings, transport))

    assert (await finished(session_factory, job_id)).status is JobStatus.SUCCESSFUL
    assert "channel-install" in transport.commands()
    assert "channel-service" not in transport.commands()


async def test_silence_starts_one_repair_on_its_own(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    """Rate-limited, so a probe that stays dead does not get a job every
    sync pass."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = probe_info()
    await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000001")]},
    )
    await last_contact_ago(session_factory, timedelta(hours=1))
    sync = build_sync(settings, transport)

    await sync.run_once()
    jobs = await repair_jobs(session_factory)
    assert [job.trigger for job in jobs] == ["system"]

    await drain(build_runner(settings, transport))
    await sync.run_once()
    await sync.run_once()
    assert len(await repair_jobs(session_factory)) == 1


async def test_no_repair_is_started_for_a_probe_management_cannot_reach(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    as_probe: None,
    transport: ScriptedTransport,
) -> None:
    from app.core.errors import ProbeUnreachableError

    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = ProbeUnreachableError.of(PROBE)
    await client.post(
        "/probe/v1/records",
        json={"records": [record("11111111-0000-4000-8000-000000000001")]},
    )
    await last_contact_ago(session_factory, timedelta(hours=1))

    await build_sync(settings, transport).run_once()

    assert "probe.channel_silent" in await alert_kinds(session_factory)
    assert await repair_jobs(session_factory) == []
