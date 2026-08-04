"""Keep the cached probe state and the alert list current.

Runs on a timer. Deliberately conservative about how often it talks to probes:
a fleet of fifty hosts polled every minute is fifty SSH handshakes a minute for
data that changes hourly. A probe is asked when its cached state has gone
stale, or when someone opens its page and presses refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import AlertSeverity, CertificateStatus, ProbeStatus
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import Alert, ProbeObservedState, ProbeRecord
from app.persistence.session import session_scope
from app.services.probes import ProbeService
from app.services.system import SystemService

logger = get_logger(__name__)

# How many probes to contact concurrently. High enough to finish a large fleet
# quickly, low enough not to open fifty SSH connections at once.
REFRESH_CONCURRENCY = 6


class InventorySync:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime: RuntimeFileStore,
        helper: ProbeHelperClient,
        catalog: SensorCatalog,
        nats: NatsMonitoringClient,
        docker: DockerAdapter,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._helper = helper
        self._catalog = catalog
        self._nats = nats
        self._docker = docker
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="inventory-sync")
        logger.info(
            "inventory sync started",
            extra={"interval": self._settings.inventory_sync_interval_seconds},
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        # A first pass right away so the dashboard is useful the moment the
        # service comes up, rather than after the first interval.
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("inventory sync pass failed")
            await asyncio.sleep(self._settings.inventory_sync_interval_seconds)

    async def run_once(self) -> None:
        """One pass, in three transactions rather than one.

        The split is not tidiness. SQLite allows a single writer, and the
        middle phase talks SSH to every stale probe - ten seconds each for
        one that does not answer. A transaction held across that locks out
        every other writer in the process for the same time: the job workers
        claiming work, and every API request, which writes the session's
        last_seen_at. Jobs then fail with "database is locked" and never
        leave the queue.
        """
        usernames = self._runtime.list_probe_usernames()

        async with session_scope() as db:
            probes = ProbeService(
                db, self._settings, self._runtime, self._helper, self._catalog
            )
            for username in usernames:
                await probes.ensure_record(username)
            stale = await self._stale_usernames(db, usernames)

        if stale:
            await self._refresh_many(stale)

        # Asked before the transaction opens, for the same reason.
        connected = await self._nats.connected_users()

        async with session_scope() as db:
            probes = ProbeService(
                db, self._settings, self._runtime, self._helper, self._catalog
            )
            system = SystemService(
                db, self._settings, self._runtime, self._nats, self._docker
            )
            summaries = await probes.list_summaries(
                connected, expected_ca_sha256=system.expected_ca_fingerprint()
            )
            await self._update_alerts(db, summaries, system)

    async def _stale_usernames(self, db, usernames: list[str]) -> list[str]:  # type: ignore[no-untyped-def]
        if not usernames:
            return []
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._settings.observed_state_stale_after_seconds
        )
        rows = await db.execute(
            select(
                ProbeRecord.nats_username,
                ProbeObservedState.observed_at,
                ProbeObservedState.refresh_due,
            )
            .outerjoin(
                ProbeObservedState, ProbeObservedState.probe_id == ProbeRecord.id
            )
            .where(ProbeRecord.nats_username.in_(usernames))
        )
        # refresh_due is set by a job that changed the probe and could not ask
        # it about the change. Waiting out the staleness window for that would
        # mean minutes of reporting a deviation the job resolved.
        return [
            username
            for username, observed_at, refresh_due in rows
            if observed_at is None or refresh_due or observed_at < cutoff
        ]

    async def _refresh_many(self, usernames: list[str]) -> None:
        semaphore = asyncio.Semaphore(REFRESH_CONCURRENCY)

        async def refresh(username: str) -> None:
            # A transaction of its own per probe, and refresh_observed_state
            # only opens it once the probe has answered. One slow host
            # therefore delays its own row and nothing else.
            async with semaphore, session_scope() as db:
                probes = ProbeService(
                    db, self._settings, self._runtime, self._helper, self._catalog
                )
                await probes.refresh_observed_state(username)

        # refresh_observed_state stores failures rather than raising, so a dead
        # probe cannot take the whole pass down with it.
        await asyncio.gather(*(refresh(name) for name in usernames))
        logger.info("refreshed probe state", extra={"count": len(usernames)})

    async def _update_alerts(self, db, summaries, system: SystemService) -> None:  # type: ignore[no-untyped-def]
        """Raise what is wrong, clear what is not.

        Alerts are derived, never authored. Clearing them here means the
        dashboard cannot show a warning about something that has been fixed.
        """
        now = datetime.now(UTC)
        wanted: dict[tuple[str, str], dict[str, Any]] = {}

        for summary in summaries:
            if summary.status is ProbeStatus.UNREACHABLE:
                wanted[("probe.unreachable", summary.nats_username)] = {
                    "severity": AlertSeverity.CRITICAL,
                    "object_type": "probe",
                    "object_label": summary.nats_username,
                    "params": {"host": summary.host},
                }
            elif summary.status is ProbeStatus.DEGRADED:
                wanted[("probe.degraded", summary.nats_username)] = {
                    "severity": AlertSeverity.WARNING,
                    "object_type": "probe",
                    "object_label": summary.nats_username,
                    "params": {"deviations": str(summary.deviation_count)},
                }

        for certificate in system.certificates():
            if certificate.status is CertificateStatus.EXPIRED:
                severity = AlertSeverity.CRITICAL
            elif certificate.status is CertificateStatus.EXPIRING_SOON:
                severity = AlertSeverity.WARNING
            elif certificate.status is CertificateStatus.MISMATCHED:
                severity = AlertSeverity.CRITICAL
            else:
                continue
            wanted[
                (f"certificate.{certificate.status.value}", certificate.kind.value)
            ] = {
                "severity": severity,
                "object_type": "certificate",
                "object_label": certificate.kind.value,
                "params": {"days_remaining": str(certificate.days_remaining or 0)},
            }

        existing = {
            (alert.kind, alert.object_ref): alert
            for alert in await db.scalars(select(Alert))
        }

        for key, data in wanted.items():
            kind, object_ref = key
            alert = existing.get(key)
            if alert is None:
                db.add(
                    Alert(
                        kind=kind,
                        severity=data["severity"],
                        object_type=data["object_type"],
                        object_ref=object_ref,
                        object_label=data["object_label"],
                        params=data["params"],
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
            else:
                alert.last_seen_at = now
                alert.severity = data["severity"]
                alert.params = data["params"]

        for key, alert in existing.items():
            if key not in wanted:
                await db.delete(alert)
