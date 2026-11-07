"""Which route a probe was last reached over, and which one its NATS takes.

After a branch office lost power its probe came back on a new address, and
whether management still reached it - over the tunnel or the site address -
could only be read from the API log. The transport already knows which
address answered (ADR 0011); these tests pin down that the answer is kept as
observed state with its time (ADR 0002) and reaches the probe's page and the
probe list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.infrastructure.probe_helper.client import ProbeConnection
from app.infrastructure.probe_helper.protocol import HelperCommand, HelperRequest
from app.infrastructure.runtime_files import RuntimeFileStore
from app.persistence import session as session_module
from app.services.management_routes import (
    flush_pending_routes,
    management_route_observer,
    record_management_route,
)
from tests.api.test_probe_access_key import probe_id_of, sign_in
from tests.conftest import write_probe_inventory

PROBE = "mpp-berlin-01"
SITE = "198.51.100.29"
OVERLAY = "10.99.0.2"


def on_overlay(project_dir: Path, last_state: str = "tunnel") -> None:
    write_probe_inventory(project_dir, PROBE, host=SITE)
    with (project_dir / "runtime" / "probes" / f"{PROBE}.env").open("a") as file:
        file.write(
            f"OVERLAY_ADDRESS={OVERLAY}\n"
            "OVERLAY_PUBLIC_KEY=cHVibGlj\n"
            "OVERLAY_MODE=auto\n"
            f"OVERLAY_LAST_STATE={last_state}\n"
        )


async def answered_on(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    host: str,
) -> None:
    """A successful call, reported the way the transport reports it."""
    store = RuntimeFileStore(settings)
    observe = management_route_observer(store, lambda: session_factory)
    connection = ProbeConnection.for_probe(store.read_probe(PROBE))
    observe(connection, host, HelperRequest(HelperCommand.STATUS), "OK status\n")
    await flush_pending_routes()


async def detail_of(client: AsyncClient) -> dict[str, object]:
    probe_id = await probe_id_of(client)
    response = await client.get(f"/api/v1/probes/{probe_id}")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_an_answer_over_the_tunnel_is_shown_as_the_management_route(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    on_overlay(project_dir)
    await sign_in(client)
    await answered_on(settings, session_factory, OVERLAY)

    detail = await detail_of(client)
    route = detail["management_route"]
    assert isinstance(route, dict)
    assert route["via"] == "tunnel"
    assert route["host"] == OVERLAY
    assert route["stale"] is False
    assert route["observed_at"]

    listing = (await client.get("/api/v1/probes")).json()
    assert listing[0]["managed_over_tunnel"] is True


async def test_an_answer_over_the_site_address_names_that_address(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    on_overlay(project_dir, last_state="down")
    await sign_in(client)
    await answered_on(settings, session_factory, SITE)

    detail = await detail_of(client)
    route = detail["management_route"]
    assert isinstance(route, dict)
    assert route["via"] == "site"
    assert route["host"] == SITE

    listing = (await client.get("/api/v1/probes")).json()
    assert listing[0]["managed_over_tunnel"] is False


async def test_the_route_survives_a_restart_of_the_api(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    on_overlay(project_dir)
    await sign_in(client)
    await answered_on(settings, session_factory, OVERLAY)

    await session_module.dispose_engine()
    session_module.init_engine(settings)

    route = (await detail_of(client))["management_route"]
    assert isinstance(route, dict)
    assert route["via"] == "tunnel"


async def test_an_old_route_is_marked_stale_and_no_longer_marks_the_list(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    on_overlay(project_dir)
    await sign_in(client)
    long_ago = datetime.now(UTC) - timedelta(
        seconds=settings.observed_state_stale_after_seconds + 60
    )
    async with session_factory() as db:
        await record_management_route(
            db, PROBE, OVERLAY, over_overlay=True, at=long_ago
        )
        await db.commit()

    route = (await detail_of(client))["management_route"]
    assert isinstance(route, dict)
    assert route["stale"] is True
    listing = (await client.get("/api/v1/probes")).json()
    assert listing[0]["managed_over_tunnel"] is False


async def test_a_newer_answer_replaces_the_route(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    on_overlay(project_dir)
    await sign_in(client)
    await answered_on(settings, session_factory, OVERLAY)
    await answered_on(settings, session_factory, SITE)

    route = (await detail_of(client))["management_route"]
    assert isinstance(route, dict)
    assert route["via"] == "site"


async def test_a_probe_never_reached_has_no_management_route(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_probe_inventory(project_dir, PROBE, host=SITE)
    await sign_in(client)

    detail = await detail_of(client)
    assert detail["management_route"] is None


async def test_the_nats_route_follows_the_overlay_peer(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await sign_in(client)

    write_probe_inventory(project_dir, PROBE, host=SITE)
    assert (await detail_of(client))["nats_route"] == "direct"

    on_overlay(project_dir, last_state="tunnel")
    assert (await detail_of(client))["nats_route"] == "tunnel"

    on_overlay(project_dir, last_state="direct")
    assert (await detail_of(client))["nats_route"] == "direct"

    # A tunnel that has not handshaked says nothing about where NATS goes.
    on_overlay(project_dir, last_state="no_handshake")
    assert (await detail_of(client))["nats_route"] is None


async def test_answers_from_targets_that_are_not_probes_are_not_recorded(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The iperf endpoints share the transport and are no probe at all."""
    on_overlay(project_dir)
    await sign_in(client)

    class Endpoint:
        label = PROBE
        host = OVERLAY
        port = 22
        fallback_host = None

    observe = management_route_observer(
        RuntimeFileStore(settings), lambda: session_factory
    )
    observe(Endpoint(), OVERLAY, HelperRequest(HelperCommand.STATUS), "OK status\n")
    await flush_pending_routes()

    assert (await detail_of(client))["management_route"] is None
