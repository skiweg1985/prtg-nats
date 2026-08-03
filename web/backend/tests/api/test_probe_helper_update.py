"""Renewing the probe helper over the management channel.

The one request that puts executable root code on a probe. What makes it
allowed at all is the signature the probe checks first, so this asserts that
the platform really signs the file it sends - not that it sends something.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeHelperOutdatedError
from app.domain.enums import JobStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"

PROBE_INFO = (
    "OK probe-info\n"
    "package=2.1.0\n"
    "service=active\n"
    "helper_version=1\n"
    "helper_sha256=aa\n"
    "hostname=berlin-probe-01.example.test\n"
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
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


async def probe_id_of(client: AsyncClient) -> str:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    return str(listing.json()[0]["id"])


async def test_the_helper_is_sent_with_a_signature_over_its_own_bytes(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["helper-update"] = "OK helper-updated version=1 sha256=bb\n"
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.post(f"/api/v1/probes/{probe_id}/helper-update")
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    # Asked before and after, so the version in the log is what the new helper
    # says about itself rather than what the old one was told to write.
    assert transport.commands() == ["probe-info", "helper-update", "probe-info"]

    _, request = transport.calls[1]
    assert request.payload is not None
    sent = request.payload.encode("utf-8")
    assert (
        sent == (settings.asset_dir / "libexec" / "prtg-nats-probe-helper").read_bytes()
    )

    public_key = serialization.load_pem_public_key(
        (settings.private_dir / "helper-signing.pub").read_bytes()
    )
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    # Raises if it does not verify, which is the assertion.
    public_key.verify(
        base64.b64decode(request.arguments[0]), sent, ec.ECDSA(hashes.SHA256())
    )

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL
        assert [step.name for step in job.steps] == [
            "check_reachable",
            "send_helper",
            "verify",
        ]


async def test_a_tampered_payload_does_not_verify(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The probe's side of the deal, checked here so the signature is not
    merely present but actually bound to the bytes."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["helper-update"] = "OK helper-updated version=1 sha256=bb\n"
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.post(f"/api/v1/probes/{probe_id}/helper-update")
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))

    _, request = transport.calls[1]
    assert request.payload is not None
    public_key = serialization.load_pem_public_key(
        (settings.private_dir / "helper-signing.pub").read_bytes()
    )
    assert isinstance(public_key, ec.EllipticCurvePublicKey)

    tampered = request.payload.encode("utf-8") + b"\nrm -rf /\n"
    try:
        public_key.verify(
            base64.b64decode(request.arguments[0]), tampered, ec.ECDSA(hashes.SHA256())
        )
    except InvalidSignature:
        return
    raise AssertionError("a changed payload verified against the signature")


async def test_an_old_probe_fails_the_job_with_its_own_error(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A helper that predates the request refuses it, and the job says which
    fix that needs - the one case the operator meets before anything else."""
    write_probe_inventory(project_dir, PROBE)
    transport.responses["probe-info"] = PROBE_INFO
    # What the SSH transport turns "Unsupported management request" into; the
    # mapping itself is covered in tests/unit/test_probe_helper_protocol.py.
    transport.responses["helper-update"] = ProbeHelperOutdatedError(
        params={"probe": PROBE, "command": "helper-update"},
        details="ERROR: Unsupported management request",
    )
    await sign_in(client)
    probe_id = await probe_id_of(client)

    accepted = await client.post(f"/api/v1/probes/{probe_id}/helper-update")
    assert accepted.status_code == 202, accepted.text
    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.FAILED
        # The code the interface looks up to tell the operator what to do.
        assert job.error_code == "probe.helper_outdated"
