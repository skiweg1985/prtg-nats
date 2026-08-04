"""What turns a probe's badge red, and what only belongs on its own page.

Two deviations are deliberately informational. An unrequested sensor is one
the platform will not remove on its own - adopting it is at least as likely to
be what the operator wants - and a name that does not match is a difference,
not a fault. Neither has a remedy the platform is entitled to choose, so
neither ever clears by itself.

The status derived from them counted deviations without weighing them, so one
adopted sensor made a probe degraded for good, with a standing warning on the
dashboard to match. A warning that cannot be cleared is one everybody learns
to ignore, which costs the ones that matter.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.domain.enums import ProbeStatus
from app.domain.models import ProbeSummary
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import Alert
from app.services.probes import ProbeDetail, ProbeService
from app.workers.inventory_sync import InventorySync
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor

PROBE = "mpp-berlin-01"
PROBE_NAME = "Example Probe"
WANTED = "internet-speed"
ADOPTED = "link-quality"

PROBE_INFO = (
    "OK probe-info\n"
    "package=2.1.0\n"
    "service=active\n"
    "ca_sha256=aa\n"
    "id=11111111-2222-3333-4444-555555555555\n"
)


def sensor_list(*names: str) -> str:
    lines = ["OK sensor-list"]
    lines.extend(
        f"{name}\tversion=1\tsha256=aa\tinterfaces=none\thelper=none" for name in names
    )
    return "\n".join(lines) + "\n"


def probe(settings: Settings, transport: ScriptedTransport, db: AsyncSession):  # type: ignore[no-untyped-def]
    return ProbeService(
        db,
        settings,
        RuntimeFileStore(settings),
        ProbeHelperClient(transport),
        SensorCatalog(settings.sensor_source_dir),
    )


async def summarise(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
) -> ProbeSummary:
    """One probe, observed and then summarised the way the list does it.

    The NATS account is handed in as connected: whether a probe is on the
    server right now is a signal of its own, and leaving it to whatever is
    listening on the test machine's monitoring port would decide the badge
    instead of the deviations under test.
    """
    async with session_factory() as db:
        service = probe(settings, transport, db)
        await service.refresh_observed_state(PROBE)
        summaries = await service.list_summaries(
            frozenset({PROBE}), expected_ca_sha256=None
        )
        await db.commit()
    return summaries[0]


async def detail_of(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    transport: ScriptedTransport,
) -> ProbeDetail:
    async with session_factory() as db:
        service = probe(settings, transport, db)
        await service.refresh_observed_state(PROBE)
        record = await service.get_record_by_username(PROBE)
        detail = await service.get_detail(
            record.id, connected_users=frozenset({PROBE}), expected_ca_sha256=None
        )
        await db.commit()
    return detail


async def test_an_adopted_sensor_does_not_make_a_probe_degraded(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A sensor on the probe that the desired state does not name."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME)
    write_sensor(project_dir, ADOPTED)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["sensor-list"] = sensor_list(ADOPTED)

    summary = await summarise(settings, session_factory, transport)

    assert summary.status is ProbeStatus.HEALTHY
    assert summary.deviation_count == 0
    # Still installed, and still counted as installed.
    assert summary.sensor_count == 1


async def test_a_name_that_does_not_match_does_not_either(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The other informational finding, on the same reasoning."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME)
    transport.responses["probe-info"] = PROBE_INFO + "name=Renamed In PRTG\n"

    summary = await summarise(settings, session_factory, transport)

    assert summary.status is ProbeStatus.HEALTHY
    assert summary.deviation_count == 0


async def test_a_missing_sensor_still_does(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The guard: this must not have quietened everything down."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME, sensors=(WANTED,))
    write_sensor(project_dir, WANTED)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["sensor-list"] = sensor_list()

    summary = await summarise(settings, session_factory, transport)

    assert summary.status is ProbeStatus.DEGRADED
    assert summary.deviation_count == 1


async def test_the_count_reports_the_one_worth_acting_on(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Both findings at once: one to act on, one to know about."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME, sensors=(WANTED,))
    write_sensor(project_dir, WANTED)
    write_sensor(project_dir, ADOPTED)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["sensor-list"] = sensor_list(ADOPTED)

    summary = await summarise(settings, session_factory, transport)

    assert summary.status is ProbeStatus.DEGRADED
    assert summary.deviation_count == 1


async def test_the_probe_page_still_says_what_it_found(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Not counted is not hidden. The finding an operator has to decide about
    is exactly the one they need to be able to see."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME)
    write_sensor(project_dir, ADOPTED)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["sensor-list"] = sensor_list(ADOPTED)

    detail = await detail_of(settings, session_factory, transport)

    assert [entry.kind.value for entry in detail.deviations] == ["sensor_unmanaged"]
    assert [entry.status.value for entry in detail.sensors] == ["unmanaged"]
    assert detail.summary.deviation_count == 0


class NatsReporting:
    """The monitoring endpoint, without one."""

    def __init__(self, *usernames: str) -> None:
        self._usernames = frozenset(usernames)

    async def connected_users(self) -> frozenset[str]:
        return self._usernames


async def test_no_standing_alert_for_a_sensor_nobody_can_clear(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The dashboard follows the badge, which is where an operator meets this
    without opening anything."""
    write_probe_inventory(project_dir, PROBE, probe_name=PROBE_NAME)
    write_sensor(project_dir, ADOPTED)
    transport.responses["probe-info"] = PROBE_INFO
    transport.responses["sensor-list"] = sensor_list(ADOPTED)

    sync = InventorySync(
        settings=settings,
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        nats=NatsReporting(PROBE),  # type: ignore[arg-type]
        docker=DockerAdapter(settings.docker_socket),
    )
    await sync.run_once()

    async with session_factory() as db:
        kinds = [alert.kind for alert in await db.scalars(select(Alert))]
    assert "probe.degraded" not in kinds, kinds
