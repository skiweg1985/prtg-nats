"""Take the device reports off NATS and answer the target requests.

Two jobs in one task. The subscriptions do the work when a probe sends
something; the loop around them exists for the two things nobody sends: the
first connection to a NATS server that was not up yet when the API started,
and the devices whose probe has gone quiet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import timedelta

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.watch import WatchProtocolError, WatchReport
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.watch_bus import WatchBus, WatchBusUnavailableError
from app.persistence.session import session_scope
from app.services.watch import WatchService

logger = get_logger(__name__)

# How often the loop wakes: to retry a connection that is not up, and to
# close the intervals of probes that stopped reporting. Both tolerate a
# minute of delay, and neither is worth a tighter loop.
TICK = timedelta(seconds=60)


class WatchIngest:
    def __init__(self, *, settings: Settings, runtime: RuntimeFileStore) -> None:
        self._settings = settings
        self._bus = WatchBus(settings, runtime)
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Logged once per outage rather than once per minute: an installation
        # that never configured NATS would otherwise fill its log with a
        # message that says nothing new.
        self._reported_unavailable = False

    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="watch-ingest")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._bus.close()

    @property
    def connected(self) -> bool:
        return self._bus.connected

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._ensure_connected()
                await self._close_silent_intervals()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("the watch ingest tripped over itself")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=TICK.total_seconds()
                )

    async def _ensure_connected(self) -> None:
        if self._bus.connected:
            return
        try:
            await self._bus.connect(
                on_report=self._handle_report, on_targets=self._handle_targets
            )
            self._reported_unavailable = False
        except WatchBusUnavailableError as error:
            if not self._reported_unavailable:
                logger.info(
                    "not receiving device reports yet",
                    extra={"reason": str(error)},
                )
                self._reported_unavailable = True

    async def _handle_report(self, account: str, payload: bytes) -> None:
        try:
            report = WatchReport.from_wire(payload)
        except WatchProtocolError as error:
            logger.warning(
                "unusable report", extra={"probe": account, "error": str(error)}
            )
            return

        if report.account != account:
            # The subject is the authority - it is the one part of the message
            # a probe cannot choose freely once accounts have subject
            # permissions, and until then it is at least consistent.
            logger.warning(
                "report claims another account",
                extra={"probe": account, "claimed": report.account},
            )
            return

        async with session_scope() as session:
            outcome = await WatchService(session).ingest(report)
        if outcome.transitions:
            logger.info(
                "device states changed",
                extra={"probe": account, "transitions": outcome.transitions},
            )

    async def _handle_targets(self, account: str, payload: bytes) -> bytes:
        known_revision = ""
        if payload:
            try:
                document = json.loads(payload)
                if isinstance(document, dict):
                    known_revision = str(document.get("revision", ""))
            except ValueError:
                # A request we cannot read still deserves the full list -
                # answering nothing would stop the probe measuring.
                known_revision = ""

        async with session_scope() as session:
            targets = await WatchService(session).targets_for_account(
                account, known_revision=known_revision
            )
        return json.dumps(targets.to_wire()).encode("utf-8")

    async def _close_silent_intervals(self) -> None:
        async with session_scope() as session:
            await WatchService(session).mark_silent_devices()
