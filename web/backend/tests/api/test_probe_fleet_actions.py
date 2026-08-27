"""One action, a selection of probes, one job.

The point of these endpoints is not that they exist but that a selection does
not behave differently from a single probe in the ways that matter: every probe
is really visited, one unreachable host does not take the rest with it, and a
selection of one still fails with its own error rather than a count.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeUnreachableError
from app.domain.enums import JobStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import (
    HelperRequest,
    HelperTarget,
    ProbeHelperClient,
)
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.audit import AuditEvent
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.services.jobs import declared_probe_ids
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory

PASSWORD = "correct-horse-battery"
BERLIN = "mpp-berlin-01"
HAMBURG = "mpp-hamburg-01"

PROBE_INFO = (
    "OK probe-info\n"
    "package=2.1.0\n"
    "service=active\n"
    "helper_version=1\n"
    "helper_sha256=aa\n"
    "hostname=probe.example.test\n"
    "config=/etc/paessler/mpprobe/config.yaml\n"
)


class PerProbeTransport(ScriptedTransport):
    """Answers by probe rather than by command.

    A fan-out is only interesting when the probes disagree, and the shared
    transport cannot express "this one is down and that one is not".
    """

    def __init__(self, by_probe: dict[str, dict[str, object]]) -> None:
        super().__init__()
        self.by_probe = by_probe

    async def run(
        self, connection: HelperTarget, request: HelperRequest, timeout: int
    ) -> str:
        self.calls.append((connection.label, request))
        answer = self.by_probe.get(connection.label, {}).get(
            request.command.value, f"OK {request.command.value}\n"
        )
        if isinstance(answer, Exception):
            raise answer
        return str(answer)


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


async def probe_ids(client: AsyncClient) -> dict[str, str]:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    return {row["nats_username"]: str(row["id"]) for row in listing.json()}


def write_two_probes(project_dir: Path) -> None:
    write_probe_inventory(
        project_dir, BERLIN, probe_id="11111111-1111-1111-1111-111111111111"
    )
    write_probe_inventory(
        project_dir, HAMBURG, probe_id="22222222-2222-2222-2222-222222222222"
    )


async def test_one_job_visits_every_selected_probe(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Twelve probes were twelve visits to twelve pages. This is the whole
    reason the endpoint exists, so it is asserted on the exchange itself."""
    write_two_probes(project_dir)
    transport = PerProbeTransport(
        {
            probe: {
                "probe-info": PROBE_INFO,
                "helper-update": "OK helper-updated version=1 sha256=bb\n",
            }
            for probe in (BERLIN, HAMBURG)
        }
    )
    await sign_in(client)
    ids = await probe_ids(client)

    accepted = await client.post(
        "/api/v1/probes/actions/helper-update",
        json={"probe_ids": [ids[BERLIN], ids[HAMBURG]]},
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    updated = [
        label
        for label, request in transport.calls
        if request.command.value == "helper-update"
    ]
    assert sorted(updated) == [BERLIN, HAMBURG]

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.SUCCESSFUL
        # One lock per probe, which is what keeps a selection from racing
        # whatever else is already working on one of them.
        assert sorted(declared_probe_ids(job)) == sorted([ids[BERLIN], ids[HAMBURG]])
        assert sorted(job.result["succeeded"]) == [BERLIN, HAMBURG]

        # One audit entry per probe: "who renewed the helper on berlin-01" has
        # to be answerable whether it came from a detail page or a selection.
        rows = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == "probe.helper_update")
            )
        )
        assert sorted(row.object_label for row in rows) == [BERLIN, HAMBURG]
        assert {row.job_id for row in rows} == {job.id}


async def test_one_unreachable_probe_does_not_take_the_others_with_it(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_two_probes(project_dir)
    transport = PerProbeTransport(
        {
            BERLIN: {"probe-info": ProbeUnreachableError.of(BERLIN)},
            HAMBURG: {
                "probe-info": PROBE_INFO,
                "helper-update": "OK helper-updated version=1 sha256=bb\n",
            },
        }
    )
    await sign_in(client)
    ids = await probe_ids(client)

    accepted = await client.post(
        "/api/v1/probes/actions/helper-update",
        json={"probe_ids": [ids[BERLIN], ids[HAMBURG]]},
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    assert [
        label
        for label, request in transport.calls
        if request.command.value == "helper-update"
    ] == [HAMBURG]

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.PARTIALLY_SUCCESSFUL
        assert job.result["succeeded"] == [HAMBURG]
        assert job.result["failed"] == [
            {"probe": BERLIN, "code": "probe.unreachable", "details": ""}
        ]


async def test_a_selection_of_one_fails_with_its_own_error(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """A count is the wrong answer when there is only one probe to name.

    "0 of 1 succeeded" with the reason buried in the log is exactly what the
    detail page does not do, and the selection route reaches the same handler.
    """
    write_two_probes(project_dir)
    transport = PerProbeTransport(
        {BERLIN: {"probe-info": ProbeUnreachableError.of(BERLIN)}}
    )
    await sign_in(client)
    ids = await probe_ids(client)

    accepted = await client.post(
        "/api/v1/probes/actions/helper-update", json={"probe_ids": [ids[BERLIN]]}
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        job = await db.get(Job, accepted.json()["job_id"])
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error_code == "probe.unreachable"


async def test_an_unknown_probe_leaves_no_job_behind(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Every id is resolved before the job exists.

    Resolving them inside the job would leave a half-applied action and a job
    to read afterwards; here the request fails and nothing has run.
    """
    write_two_probes(project_dir)
    await sign_in(client)
    ids = await probe_ids(client)

    response = await client.post(
        "/api/v1/probes/actions/install-ca",
        json={"probe_ids": [ids[BERLIN], "no-such-probe"]},
    )
    assert response.status_code == 404, response.text

    async with session_factory() as db:
        assert list(await db.scalars(select(Job))) == []


async def test_an_empty_selection_is_refused(
    client: AsyncClient, project_dir: Path
) -> None:
    write_two_probes(project_dir)
    await sign_in(client)

    response = await client.post(
        "/api/v1/probes/actions/validate", json={"probe_ids": []}
    )
    assert response.status_code == 422, response.text


async def test_refreshing_a_selection_writes_the_state_it_read(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
) -> None:
    """The one action that is synchronous for a single probe.

    A dozen SSH round trips is not something to hold a request open for, so it
    became a job - and the job has to leave the same thing behind that the
    synchronous route does: an observation the list can render.
    """
    write_two_probes(project_dir)
    transport = PerProbeTransport(
        {probe: {"probe-info": PROBE_INFO} for probe in (BERLIN, HAMBURG)}
    )
    await sign_in(client)
    ids = await probe_ids(client)

    accepted = await client.post(
        "/api/v1/probes/actions/refresh",
        json={"probe_ids": [ids[BERLIN], ids[HAMBURG]]},
    )
    assert accepted.status_code == 202, accepted.text

    await drain(build_runner(settings, transport))

    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    observed = {row["nats_username"]: row for row in listing.json()}
    assert observed[BERLIN]["package_version"] == "2.1.0"
    assert observed[HAMBURG]["package_version"] == "2.1.0"
