"""Which route a probe last answered management on - issue #135.

The transport reports the address that answered after every successful call
(ADR 0011). This keeps it as observed state with its time, per ADR 0002: a
cache the probe's page reads, not something written into the inventory.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.domain.models import ManagementRoute
from app.infrastructure.probe_helper.client import (
    AnswerObserver,
    HelperTarget,
    SshHelperTransport,
)
from app.infrastructure.probe_helper.management_transport import (
    both,
    inventory_route_observer,
)
from app.infrastructure.probe_helper.protocol import HelperRequest
from app.infrastructure.runtime_files import RuntimeFileStore
from app.persistence.models.inventory import ProbeManagementRoute, ProbeRecord
from app.persistence.session import get_session_factory

logger = get_logger(__name__)

SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession] | None]

# Held so a scheduled write is not collected half-way; asyncio keeps only a
# weak reference to a task nobody awaits.
_pending: set[asyncio.Task[None]] = set()


async def record_management_route(
    session: AsyncSession, nats_username: str, route: ManagementRoute
) -> None:
    """Replace the probe's last route with this one."""
    record = await session.scalar(
        select(ProbeRecord).where(ProbeRecord.nats_username == nats_username)
    )
    if record is None:
        # First sight of a probe the inventory names, the same as listing it.
        record = ProbeRecord(nats_username=nats_username)
        session.add(record)
        await session.flush()
    row = await session.scalar(
        select(ProbeManagementRoute).where(ProbeManagementRoute.probe_id == record.id)
    )
    if row is None:
        session.add(
            ProbeManagementRoute(
                probe_id=record.id,
                host=route.host,
                over_overlay=route.over_overlay,
                observed_at=route.observed_at,
            )
        )
        return
    row.host = route.host
    row.over_overlay = route.over_overlay
    row.observed_at = route.observed_at


def management_route_observer(
    store: RuntimeFileStore, session_factory: SessionFactoryProvider
) -> AnswerObserver:
    """An observer that records the route of every answer from a probe.

    The write is scheduled, not awaited. The call that answered may sit inside
    a transaction that already holds SQLite's write lock, and waiting for that
    lock from inside the same call would stall it until the busy timeout.
    """

    def observe(
        connection: HelperTarget, answered: str, request: HelperRequest, output: str
    ) -> None:
        username = getattr(connection, "nats_username", None)
        if not username:
            return
        try:
            inventory = store.read_probe(username)
        except AppError:
            return
        over_overlay = bool(inventory.overlay_address) and (
            answered == inventory.overlay_address
        )
        factory = session_factory()
        if factory is None:
            return
        route = ManagementRoute(
            host=answered, over_overlay=over_overlay, observed_at=datetime.now(UTC)
        )
        task = asyncio.get_running_loop().create_task(_write(factory, username, route))
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    return observe


async def _write(
    factory: async_sessionmaker[AsyncSession], username: str, route: ManagementRoute
) -> None:
    try:
        async with factory() as session:
            await record_management_route(session, username, route)
            await session.commit()
    except IntegrityError:
        # Two answers from the same probe at the same moment, both creating
        # the row. The other one is just as recent; nothing is lost.
        pass
    except Exception:
        logger.exception(
            "could not record which route the probe answered on",
            extra={"probe": username, "host": route.host},
        )


async def flush_pending_routes() -> None:
    """Wait for the writes scheduled so far, for a caller that reads them next."""
    while _pending:
        await asyncio.gather(*list(_pending))


def default_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """The application's factory, or None where no database is set up - the
    command-line tooling reaches probes through the same transport."""
    try:
        return get_session_factory()
    except RuntimeError:
        return None


def management_transport(settings: Settings) -> SshHelperTransport:
    """The transport every probe call goes through, with the observers wired:
    the inventory learns a moved probe's address, the database which route
    answered (issue #135).

    Assembled here rather than beside the transport: recording the route needs
    the database, and the infrastructure layer stays free of services."""
    store = RuntimeFileStore(settings)
    return SshHelperTransport(
        key_path=settings.ssh_key_path,
        known_hosts_path=settings.ssh_known_hosts_path,
        connect_timeout=settings.ssh_connect_timeout_seconds,
        on_answer=both(
            inventory_route_observer(store),
            management_route_observer(store, default_session_factory),
        ),
    )
