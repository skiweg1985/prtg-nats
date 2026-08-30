"""A job changes a probe; the platform has to notice before the badge does.

The desired state grows the moment a rollout commits - the sensor is assigned
from then on. What the platform *knows* about the probe was read before the job
ran, and comparing the two produces a deviation the job has just resolved: the
sensor it installed a moment ago, reported as missing. The probe then shows
degraded, with an alert to match, until the cached observation expires on its
own - five minutes by default.

So the runner asks the probes a job held how it left them. When the probe
cannot be asked - a host still restarting its service - the cache is marked for
the next sync pass instead of being overwritten with "unreachable", which would
trade a wrong warning for a wrong alarm.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeUnreachableError
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import (
    HelperRequest,
    ProbeConnection,
    ProbeHelperClient,
)
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import ProbeObservedState
from app.services.events import get_broadcaster
from app.services.probes import ProbeService
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"
OTHER_PROBE = "mpp-hamburg-01"
SENSOR = "internet-speed"
SENSOR_VERSION = "2"

# Everything _derive_status needs to call a probe healthy: reachable, service
# up, an identity. No CA is set up in these tests, so the CA state is unknown
# rather than missing, which is not a deviation.
PROBE_INFO = (
    "OK probe-info\n"
    "package=2.1.0\n"
    "service=active\n"
    "helper_version=8\n"
    "ca_sha256=aa\n"
    "id=11111111-2222-3333-4444-555555555555\n"
)


def catalogue_sha256(settings: Settings) -> str:
    """What the catalogue says the sensor script hashes to.

    The probe has to report the file the rollout actually put there, or the
    comparison finds drift instead of the missing sensor this is about.
    """
    script = SensorCatalog(settings.sensor_source_dir).get(SENSOR).file_for("script")
    assert script is not None
    return script.sha256


def sensor_list(sha256: str, *names: str) -> str:
    """The probe's own answer, in the shape the helper prints it."""
    lines = ["OK sensor-list"]
    lines.extend(
        f"{name}\tversion={SENSOR_VERSION}\tsha256={sha256}"
        "\tinterfaces=none\thelper=none"
        for name in names
    )
    return "\n".join(lines) + "\n"


class Probe(ScriptedTransport):
    """A probe whose sensor list reflects what has actually been committed.

    The point of the fix is that the platform reads the list *after* the
    rollout. A transport that answers the same thing before and after could not
    tell whether it ever asked again.
    """

    def __init__(self, sha256: str) -> None:
        super().__init__({"probe-info": PROBE_INFO})
        self.sha256 = sha256
        self.installed: list[str] = []

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        answer = await super().run(connection, request, timeout)
        if request.command.value == "sensor-commit":
            self.installed.append(SENSOR)
        if request.command.value == "sensor-list":
            return sensor_list(self.sha256, *self.installed)
        return answer


class ProbeThatStopsAnswering(Probe):
    """Answers while the job runs and not afterwards.

    The MPP service restarting is the everyday version of this: the job did
    exactly what it was asked to, and the host is not ready to talk about it
    yet.
    """

    def __init__(self, sha256: str) -> None:
        super().__init__(sha256)
        self._done = False

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        if self._done:
            self.calls.append((connection.nats_username, request))
            raise ProbeUnreachableError.of(connection.nats_username)
        answer = await super().run(connection, request, timeout)
        if request.command.value == "sensor-commit":
            self._done = True
        return answer


class ProbeStillRestarting(Probe):
    """Answers the whole time, but reports the service as down afterwards.

    The management channel runs over SSH and is back long before the MPP is,
    so the platform gets a complete answer that happens to say the one thing
    it cannot take at face value right after a rollout.
    """

    def __init__(self, sha256: str) -> None:
        super().__init__(sha256)
        self._done = False

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        answer = await super().run(connection, request, timeout)
        if request.command.value == "sensor-commit":
            self._done = True
        if self._done and request.command.value == "probe-info":
            return PROBE_INFO.replace("service=active", "service=inactive")
        return answer


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


async def summary_of(client: AsyncClient, username: str) -> dict[str, object]:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    row = next(entry for entry in listing.json() if entry["nats_username"] == username)
    return dict(row)


async def deploy(client: AsyncClient, probe_id: str) -> None:
    created = await client.post(
        "/api/v1/deployments", json={"sensor": SENSOR, "probe_ids": [probe_id]}
    )
    assert created.status_code == 202, created.text


async def test_a_rollout_does_not_leave_the_probe_reported_as_degraded(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
) -> None:
    """The whole bug, from the outside: deploy a sensor, read the badge.

    Deliberately on the deviation count rather than the status word: the badge
    also carries whether the probe is on NATS right now, which no fixture here
    controls. A deviation is what turns a working probe degraded, and it is
    what this changed.
    """
    write_probe_inventory(project_dir, PROBE)
    write_sensor(project_dir, SENSOR, version=SENSOR_VERSION)
    transport = Probe(catalogue_sha256(settings))
    await sign_in(client)

    summary = await summary_of(client, PROBE)
    await deploy(client, str(summary["id"]))
    await drain(build_runner(settings, transport))

    summary = await summary_of(client, PROBE)
    assert summary["deviation_count"] == 0, (
        "the sensor the job just installed is still being reported as missing"
    )
    assert summary["sensor_count"] == 1
    assert summary["stale"] is False


async def test_the_deviation_is_gone_from_the_detail_too(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
) -> None:
    """The list counts deviations; the detail names them."""
    write_probe_inventory(project_dir, PROBE)
    write_sensor(project_dir, SENSOR, version=SENSOR_VERSION)
    transport = Probe(catalogue_sha256(settings))
    await sign_in(client)

    probe_id = str((await summary_of(client, PROBE))["id"])
    await deploy(client, probe_id)
    await drain(build_runner(settings, transport))

    detail = (await client.get(f"/api/v1/probes/{probe_id}")).json()
    kinds = [entry["kind"] for entry in detail["deviations"]]
    assert "sensor_missing" not in kinds, kinds
    assert [entry["status"] for entry in detail["sensors"]] == ["current"]


async def test_only_the_probes_the_job_held_are_asked(
    client: AsyncClient,
    settings: Settings,
    project_dir: Path,
) -> None:
    """A rollout to one probe must not open a connection to the whole fleet."""
    write_probe_inventory(project_dir, PROBE)
    write_probe_inventory(project_dir, OTHER_PROBE, host="hamburg.example.test")
    write_sensor(project_dir, SENSOR, version=SENSOR_VERSION)
    transport = Probe(catalogue_sha256(settings))
    await sign_in(client)

    await deploy(client, str((await summary_of(client, PROBE))["id"]))
    await drain(build_runner(settings, transport))

    assert {username for username, _ in transport.calls} == {PROBE}


async def test_a_probe_that_stops_answering_keeps_its_last_good_state(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Storing "unreachable" here would be a worse lie than the one it fixes.

    The job succeeded and the host is coming back up. Writing it down as down
    would report a working probe as unreachable for the whole staleness window,
    and raise a critical alert about it.
    """
    write_probe_inventory(project_dir, PROBE)
    write_sensor(project_dir, SENSOR, version=SENSOR_VERSION)
    transport = ProbeThatStopsAnswering(catalogue_sha256(settings))
    await sign_in(client)

    probe_id = str((await summary_of(client, PROBE))["id"])
    # Seed the cache with a good answer, the way a sync pass would have. Not
    # through the refresh endpoint: the API builds its own helper client and
    # would go looking for a real host over SSH.
    async with session_factory() as db:
        probes = ProbeService(
            db,
            settings,
            RuntimeFileStore(settings),
            ProbeHelperClient(transport),
            SensorCatalog(settings.sensor_source_dir),
        )
        seeded = await probes.refresh_observed_state(PROBE)
        assert seeded.reachable
        await db.commit()

    await deploy(client, probe_id)
    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        row = await db.scalar(select(ProbeObservedState))
        assert row is not None
        assert row.reachable, "a probe that did not answer was written down as down"
        assert row.error_code is None
        assert row.refresh_due, "nothing will ask this probe again before it expires"

    assert (await summary_of(client, PROBE))["status"] != "unreachable"


async def test_a_service_still_coming_up_is_asked_again_within_the_minute(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """An answer of "inactive" seconds after a restart is not worth five minutes.

    Where a rollout changed the NATS account, the old process ignores SIGTERM
    while it retries a connection the server no longer accepts, and systemd
    spends its full stop timeout before the new one starts. Asked in that
    window, the probe truthfully says the service is down - and the interface
    would repeat it until the observation goes stale.
    """
    write_probe_inventory(project_dir, PROBE)
    write_sensor(project_dir, SENSOR, version=SENSOR_VERSION)
    transport = ProbeStillRestarting(catalogue_sha256(settings))
    await sign_in(client)

    probe_id = str((await summary_of(client, PROBE))["id"])
    await deploy(client, probe_id)
    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        row = await db.scalar(select(ProbeObservedState))
        assert row is not None
        assert row.reachable, "the probe answered; only its service had not"
        assert row.refresh_due, "the next sync pass will not correct this in a minute"
