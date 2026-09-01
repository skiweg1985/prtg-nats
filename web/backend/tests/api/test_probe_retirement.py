"""Retiring a probe, with and without clearing what was put on it.

The order these steps run in is the whole point: everything that needs the
management channel has to be done before the channel is closed, and the NATS
account can only go after the inventory that names it. A ScriptedTransport
stands in for the probe, so the sequence is asserted rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeUnreachableError
from app.domain.enums import JobStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"


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
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


def sensor_list(*names: str) -> str:
    """The probe's own answer, in the shape the helper prints it."""
    lines = ["OK sensor-list"]
    lines.extend(
        f"{name}\tversion=1\tsha256=aa\tinterfaces=none\thelper=none" for name in names
    )
    return "\n".join(lines) + "\n"


async def probe_id_of(client: AsyncClient) -> str:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    return str(listing.json()[0]["id"])


async def test_a_full_cleanup_clears_the_probe_before_it_revokes_access(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Sensors and the probe software go while the channel is still open."""
    write_probe_inventory(project_dir, PROBE, sensors=("internet-speed",))
    write_sensor(project_dir, "internet-speed")
    transport.responses["mpp-uninstall"] = (
        "OK mpp-uninstalled package=2.1.0 state=purged repositories=2 sensors=0\n"
    )
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.request(
        "DELETE",
        f"/api/v1/probes/{probe_id}",
        params={"remove_sensors": True, "uninstall_mpp": True},
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    assert transport.commands() == [
        "sensor-list",
        "sensor-remove",
        "mpp-uninstall",
        "unenroll",
    ]

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL
        assert [step.name for step in job.steps] == [
            "remove_sensors",
            "uninstall_mpp",
            "revoke_access",
            "remove_inventory",
        ]

    probes = project_dir / "runtime" / "probes"
    assert not (probes / f"{PROBE}.env").exists()
    assert not (probes / f"{PROBE}.sensors").exists()
    # The account is a separate decision and was not asked for here.
    assert (project_dir / "runtime" / "credentials" / f"{PROBE}.env").is_file()


async def test_a_sensor_only_the_probe_knows_about_is_removed_too(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """An interrupted rollout leaves a sensor the bookkeeping never recorded.

    Cleaning up from the inventory alone would leave exactly those behind.
    """
    write_probe_inventory(project_dir, PROBE, sensors=("internet-speed",))
    write_sensor(project_dir, "internet-speed")
    write_sensor(project_dir, "link-quality")
    transport.responses["sensor-list"] = sensor_list("link-quality")
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.request(
        "DELETE", f"/api/v1/probes/{probe_id}", params={"remove_sensors": True}
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    removed = [
        request.arguments[0]
        for _, request in transport.calls
        if request.command.value == "sensor-remove"
    ]
    assert sorted(removed) == ["internet-speed", "link-quality"]


async def test_a_probe_that_cannot_be_cleaned_up_keeps_its_access(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Revoking access after a failed cleanup would strand the leftovers.

    Nobody could reach the host again to finish the job, so the failure has
    to stop before the channel is closed.
    """
    write_probe_inventory(project_dir, PROBE, sensors=("internet-speed",))
    write_sensor(project_dir, "internet-speed")
    transport.responses["sensor-list"] = ProbeUnreachableError.of(PROBE)
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.request(
        "DELETE", f"/api/v1/probes/{probe_id}", params={"remove_sensors": True}
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    assert "unenroll" not in transport.commands()
    assert (project_dir / "runtime" / "probes" / f"{PROBE}.env").is_file()

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.FAILED


async def test_the_last_nats_account_survives_the_probe_that_used_it(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Refused before the job starts, not once the access is already gone."""
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    probe_id = await probe_id_of(client)

    refused = await client.request(
        "DELETE", f"/api/v1/probes/{probe_id}", params={"delete_account": True}
    )
    assert refused.status_code == 409, refused.text
    assert transport.commands() == []
    assert (project_dir / "runtime" / "probes" / f"{PROBE}.env").is_file()


async def test_a_plain_unenroll_still_only_revokes_and_forgets(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The default stays what it was: the probe keeps running and reporting."""
    write_probe_inventory(project_dir, PROBE, sensors=("internet-speed",))
    write_sensor(project_dir, "internet-speed")
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.request("DELETE", f"/api/v1/probes/{probe_id}")
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    assert transport.commands() == ["unenroll"]
    assert (project_dir / "runtime" / "credentials" / f"{PROBE}.env").is_file()


async def test_retiring_a_probe_takes_its_overlay_peer_with_it(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Observed in production: it did not.

    The hub is rendered from the inventory, and the retirement removed the
    entry without writing the file again - so the running hub kept the peer.
    A probe an operator had retired stayed on the overlay, with a route to
    the NATS address, indefinitely.

    Retiring takes the network access too. Everything else about a plain
    unenrolment is unchanged: the probe keeps running, it just has no way in.
    """
    from app.infrastructure.overlay import (
        OverlayRuntime,
        OverlaySettings,
        generate_keypair,
    )
    from app.infrastructure.runtime_files import RuntimeFileStore

    write_probe_inventory(project_dir, PROBE)
    overlay = OverlayRuntime(settings)
    overlay.write_settings(
        OverlaySettings(enabled=True, endpoint_host="vpn.example.test")
    )
    overlay.ensure_hub_key()
    _, public_key = generate_keypair()
    RuntimeFileStore(settings).write_probe_overlay(
        PROBE, address="10.83.1.0", public_key=public_key, mode="on"
    )
    overlay.write_hub_config()
    assert public_key in overlay.config_path.read_text(encoding="utf-8")

    await sign_in(client)
    probe_id = await probe_id_of(client)
    accepted = await client.request("DELETE", f"/api/v1/probes/{probe_id}")
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    # The file the hub actually reads, not a fresh rendering - that file is
    # the only thing that revokes anything.
    assert public_key not in overlay.config_path.read_text(encoding="utf-8")
