"""Choosing a test interface without typing its name.

Reserving one is not a note in a database: it takes the interface away from
NetworkManager for good and cuts whatever it was carrying. Somebody has to
decide that, and deciding it blind - from a name typed into a form - is how a
probe loses the link it was actually using.

So the probe is asked what it has, and the answer carries what would be lost:
which sensor already holds an interface, whether it is on the default route,
what it is connected to right now. None of it is a verdict. Refusing belongs
to the probe, which is where reserve_sensor_interface already does it, and a
second opinion here would only drift away from the first.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.probe_helper.protocol import HelperCommand
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.services.probes import ProbeService
from tests.conftest import ScriptedTransport, write_probe_inventory

PROBE = "mpp-berlin-01"

# Three interfaces, three situations: one already held by a sensor, one
# carrying the host's route out, one idle and free to take.
INTERFACES = (
    "OK wireless-interfaces\n"
    "wlan0\treserved=wlan-auth\tdefault_route=no\toperstate=down"
    "\tnm_state=unmanaged\tconnection=none\n"
    "wlan1\treserved=none\tdefault_route=yes\toperstate=up"
    "\tnm_state=connected\tconnection=Uplink\n"
    "wlan2\treserved=none\tdefault_route=no\toperstate=down"
    "\tnm_state=disconnected\tconnection=none\n"
)


def service(
    settings: Settings, transport: ScriptedTransport, db: AsyncSession
) -> ProbeService:
    return ProbeService(
        db,
        settings,
        RuntimeFileStore(settings),
        ProbeHelperClient(transport),
        SensorCatalog(settings.sensor_source_dir),
    )


async def test_the_probe_reports_what_a_reservation_would_cost(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    transport.responses[HelperCommand.WIRELESS_INTERFACES.value] = INTERFACES

    async with session_factory() as db:
        interfaces = await service(settings, transport, db).wireless_interfaces(PROBE)

    assert [entry.name for entry in interfaces] == ["wlan0", "wlan1", "wlan2"]

    held, uplink, free = interfaces
    assert held.reserved_by == "wlan-auth"
    assert held.nm_state == "unmanaged"

    # The one nobody may take, and the reason it may not - stated, not judged.
    assert uplink.carries_default_route is True
    assert uplink.connection == "Uplink"
    assert uplink.reserved_by is None

    # "none" is how the helper writes an empty field in a tab-separated line.
    # Read literally it would name a sensor called none and a connection to a
    # network called none, and the free interface would look like neither.
    assert free.reserved_by is None
    assert free.connection is None
    assert free.carries_default_route is False


async def test_a_probe_without_a_radio_answers_with_nothing(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """An empty list is an answer, not a failure.

    Most probes are wired only. Asking them is what the sensor page does
    before it knows, and it has to come back empty rather than red.
    """
    write_probe_inventory(project_dir, PROBE)
    transport.responses[HelperCommand.WIRELESS_INTERFACES.value] = (
        "OK wireless-interfaces\n"
    )

    async with session_factory() as db:
        interfaces = await service(settings, transport, db).wireless_interfaces(PROBE)

    assert interfaces == ()
