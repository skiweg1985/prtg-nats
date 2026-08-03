"""The inventory sync must not hold the write lock while it waits for a probe.

SQLite allows one writer. The sync talks SSH to every stale probe, and an
unreachable one takes the connect timeout to answer. A transaction held across
that call locks out every other writer in the process - the job workers
claiming work, and every API request, which writes its session's last_seen_at.
The visible result was jobs failing with "database is locked" and never
leaving the queue after a probe was unenrolled.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeUnreachableError
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.probe_helper import (
    HelperRequest,
    ProbeConnection,
    ProbeHelperClient,
)
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import ProbeObservedState
from app.workers.inventory_sync import InventorySync
from tests.conftest import write_probe_inventory

PROBE = "mpp-berlin-01"
# Long enough to measure against, short enough not to slow the suite down.
HELPER_DELAY = 2.0


class SlowTransport:
    """A probe that takes its time, the way an unreachable one does."""

    def __init__(self, delay: float, *, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ProbeUnreachableError.of(connection.nats_username)
        return f"OK {request.command.value}\n"


def build_sync(settings: Settings, transport: SlowTransport) -> InventorySync:
    return InventorySync(
        settings=settings,
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        nats=NatsMonitoringClient(settings.nats_monitoring_url),
        docker=DockerAdapter(settings.docker_socket),
    )


async def write_lock_is_free(database: Path) -> bool:
    """Whether another writer could get in right now."""
    connection = await aiosqlite.connect(str(database))
    try:
        # Short on purpose: this asks "is it free", not "wait until it is".
        await connection.execute("PRAGMA busy_timeout=300")
        try:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.execute("ROLLBACK")
            return True
        except Exception:
            return False
    finally:
        await connection.close()


async def test_a_pass_leaves_the_write_lock_alone_while_it_waits(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    database = project_dir / "test.db"
    write_probe_inventory(project_dir, PROBE)
    sync = build_sync(settings, SlowTransport(HELPER_DELAY))

    pass_task = asyncio.create_task(sync.run_once())
    # Far enough in that the bookkeeping is done and the pass is sitting in
    # the helper call, which is where a real one spends its time.
    await asyncio.sleep(HELPER_DELAY / 2)
    free_during_the_wait = await write_lock_is_free(database)
    await pass_task

    assert free_during_the_wait, (
        "the pass held SQLite's write lock while waiting for a probe; "
        "every job worker would fail with 'database is locked'"
    )


async def test_an_unreachable_probe_does_not_block_the_writers_either(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The case that produced the incident: a probe that never answers."""
    database = project_dir / "test.db"
    write_probe_inventory(project_dir, PROBE)
    sync = build_sync(settings, SlowTransport(HELPER_DELAY, fail=True))

    pass_task = asyncio.create_task(sync.run_once())
    await asyncio.sleep(HELPER_DELAY / 2)
    free_during_the_wait = await write_lock_is_free(database)
    await pass_task

    assert free_during_the_wait

    # The failure still has to be recorded, or the table shows an empty cell
    # instead of "unreachable since".
    async with session_factory() as db:
        observed = await db.scalar(select_observed())
        assert observed is not None
        assert not observed.reachable


def select_observed():  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return select(ProbeObservedState)


async def test_a_pass_still_records_what_the_probe_reported(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Splitting the transactions must not lose the result of the pass."""
    write_probe_inventory(project_dir, PROBE)
    sync = build_sync(settings, SlowTransport(0.0))

    await sync.run_once()

    async with session_factory() as db:
        observed = await db.scalar(select_observed())
        assert observed is not None
        assert observed.observed_at is not None


async def test_the_lock_is_measurably_free_for_the_whole_wait(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Not just at one sampled moment - for the length of the helper call."""
    database = project_dir / "test.db"
    write_probe_inventory(project_dir, PROBE)
    sync = build_sync(settings, SlowTransport(HELPER_DELAY))

    pass_task = asyncio.create_task(sync.run_once())
    blocked_for = 0.0
    started = time.monotonic()
    while not pass_task.done() and time.monotonic() - started < HELPER_DELAY:
        if not await write_lock_is_free(database):
            blocked_for += 0.3
        await asyncio.sleep(0.1)
    await pass_task

    # The short writes at either end of the pass are fine; a wait-length hold
    # is not.
    assert blocked_for < HELPER_DELAY / 2, (
        f"the write lock was unavailable for {blocked_for:.1f}s during a "
        f"{HELPER_DELAY:.1f}s pass"
    )
