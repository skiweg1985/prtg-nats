"""Probe reads and probe state.

The inventory in ``runtime/probes/`` stays authoritative for which probes exist
and how to reach them. This service pairs each inventory entry with the record
that carries the web platform's own additions - display name, labels, desired
state - and with the last thing the probe said about itself.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, ProbeUnreachableError
from app.core.logging import get_logger
from app.domain.enums import (
    CaState,
    NatsConnectionState,
    ProbeStatus,
    ServiceState,
)
from app.domain.models import (
    DesiredProbeState,
    Deviation,
    InstalledSensor,
    ObservedProbeState,
    ProbeSummary,
    parse_probe_info,
    parse_sensor_list,
)
from app.domain.reconciliation import (
    ReconciliationPlan,
    SensorComparison,
    build_plan,
    compare_sensors,
    find_deviations,
)
from app.infrastructure.probe_helper import ProbeConnection, ProbeHelperClient
from app.infrastructure.runtime_files import ProbeInventory, RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import (
    ProbeDesiredState,
    ProbeObservedState,
    ProbeRecord,
)
from app.persistence.models.jobs import Job, ResourceLock

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProbeDetail:
    record: ProbeRecord
    inventory: ProbeInventory
    observed: ObservedProbeState | None
    desired: DesiredProbeState
    summary: ProbeSummary
    sensors: tuple[SensorComparison, ...]
    deviations: tuple[Deviation, ...]


class ProbeService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        runtime: RuntimeFileStore,
        helper: ProbeHelperClient,
        catalog: SensorCatalog,
    ) -> None:
        self._db = session
        self._settings = settings
        self._runtime = runtime
        self._helper = helper
        self._catalog = catalog

    # --- Identity -----------------------------------------------------------

    async def ensure_record(self, nats_username: str) -> ProbeRecord:
        """Every probe in the inventory gets a stable id here, on first sight."""
        record = await self._db.scalar(
            select(ProbeRecord).where(ProbeRecord.nats_username == nats_username)
        )
        if record is None:
            record = ProbeRecord(nats_username=nats_username)
            self._db.add(record)
            await self._db.flush()
        return record

    async def get_record(self, probe_id: str) -> ProbeRecord:
        record = await self._db.get(ProbeRecord, probe_id)
        if record is None:
            raise NotFoundError.of("probe", probe_id)
        return record

    async def get_record_by_username(self, nats_username: str) -> ProbeRecord:
        record = await self._db.scalar(
            select(ProbeRecord).where(ProbeRecord.nats_username == nats_username)
        )
        if record is None:
            raise NotFoundError.of("probe", nats_username)
        return record

    def inventory_for(self, nats_username: str) -> ProbeInventory:
        """The runtime inventory of one probe, for callers that need the raw
        record rather than the summarised view."""
        return self._runtime.read_probe(nats_username)

    @staticmethod
    def connection_for(inventory: ProbeInventory) -> ProbeConnection:
        return ProbeConnection(
            nats_username=inventory.nats_username,
            host=inventory.ssh_host,
            port=inventory.ssh_port,
        )

    # --- Listing ------------------------------------------------------------

    async def list_summaries(
        self, connected_users: frozenset[str], *, expected_ca_sha256: str | None
    ) -> list[ProbeSummary]:
        """The probe table, entirely from cached state.

        No SSH happens here. A list view that opens one connection per row
        takes as long as the slowest unreachable probe, which is precisely the
        row an operator opened the page for.
        """
        inventories = {
            inventory.nats_username: inventory
            for inventory in self._runtime.read_all_probes()
        }
        if not inventories:
            return []

        records = {
            record.nats_username: record
            for record in await self._db.scalars(
                select(ProbeRecord).where(
                    ProbeRecord.nats_username.in_(inventories.keys())
                )
            )
        }
        for username in inventories:
            if username not in records:
                records[username] = await self.ensure_record(username)

        observed_rows = {
            row.probe_id: row
            for row in await self._db.scalars(
                select(ProbeObservedState).where(
                    ProbeObservedState.probe_id.in_(
                        record.id for record in records.values()
                    )
                )
            )
        }
        active_jobs = await self._active_job_ids()
        catalogue_versions = {
            definition.name: definition.version for definition in self._catalog.list()
        }

        summaries: list[ProbeSummary] = []
        for username, inventory in sorted(inventories.items()):
            record = records[username]
            observed = self._observed_from_row(username, observed_rows.get(record.id))
            desired = await self._desired_for(record)
            deviation_count = 0
            if observed is not None and observed.reachable:
                deviation_count = len(
                    find_deviations(
                        desired,
                        observed,
                        catalogue_versions=catalogue_versions,
                        expected_ca_sha256=expected_ca_sha256,
                    )
                )
            summaries.append(
                self._summarise(
                    record=record,
                    inventory=inventory,
                    observed=observed,
                    connected_users=connected_users,
                    expected_ca_sha256=expected_ca_sha256,
                    deviation_count=deviation_count,
                    running_job_id=active_jobs.get(record.id),
                )
            )
        return summaries

    async def get_detail(
        self,
        probe_id: str,
        *,
        connected_users: frozenset[str],
        expected_ca_sha256: str | None,
    ) -> ProbeDetail:
        record = await self.get_record(probe_id)
        inventory = self._runtime.read_probe(record.nats_username)
        row = await self._db.scalar(
            select(ProbeObservedState).where(ProbeObservedState.probe_id == record.id)
        )
        observed = self._observed_from_row(record.nats_username, row)
        desired = await self._desired_for(record)

        definitions = self._catalog.list()
        catalogue_versions = {d.name: d.version for d in definitions}
        catalogue_checksums = {
            d.name: script.sha256
            for d in definitions
            if (script := d.file_for("script")) is not None
        }

        sensors: tuple[SensorComparison, ...] = ()
        deviations: tuple[Deviation, ...] = ()
        if observed is not None:
            sensors = tuple(
                compare_sensors(
                    desired,
                    observed,
                    catalogue_versions=catalogue_versions,
                    catalogue_checksums=catalogue_checksums,
                )
            )
            deviations = tuple(
                find_deviations(
                    desired,
                    observed,
                    catalogue_versions=catalogue_versions,
                    catalogue_checksums=catalogue_checksums,
                    expected_ca_sha256=expected_ca_sha256,
                )
            )

        active_jobs = await self._active_job_ids()
        summary = self._summarise(
            record=record,
            inventory=inventory,
            observed=observed,
            connected_users=connected_users,
            expected_ca_sha256=expected_ca_sha256,
            deviation_count=len(deviations),
            running_job_id=active_jobs.get(record.id),
        )
        return ProbeDetail(
            record=record,
            inventory=inventory,
            observed=observed,
            desired=desired,
            summary=summary,
            sensors=sensors,
            deviations=deviations,
        )

    # --- Talking to the probe ----------------------------------------------

    async def refresh_observed_state(self, nats_username: str) -> ObservedProbeState:
        """Ask a probe how it is, and remember the answer.

        Failure is a result, not an exception: "unreachable since 14:02" is
        exactly what the operator needs to see, and raising here would leave
        the table with an empty cell instead.
        """
        inventory = self._runtime.read_probe(nats_username)
        connection = self.connection_for(inventory)
        now = datetime.now(UTC)

        # The probe is asked before anything is written. ensure_record flushes,
        # which opens SQLite's single write transaction - held across the helper
        # call it would lock out every other writer for as long as an
        # unreachable host takes to time out.
        try:
            info = await self._helper.probe_info(connection)
            observed = parse_probe_info(nats_username, info, now)
            try:
                sensor_response = await self._helper.sensor_list(connection)
                observed = _with_sensors(observed, parse_sensor_list(sensor_response))
            except ProbeUnreachableError:
                # The probe answered probe-info a moment ago; a sensor-list
                # failure is worth noting but does not make the probe unknown.
                logger.warning(
                    "sensor list unavailable", extra={"probe": nats_username}
                )
        except Exception as exc:
            code = getattr(exc, "code", "probe.unreachable")
            observed = ObservedProbeState(
                nats_username=nats_username,
                observed_at=now,
                reachable=False,
                error_code=code,
                error_details=str(getattr(exc, "details", None) or exc)[:2000],
            )

        record = await self.ensure_record(nats_username)
        await self._store_observed(record, observed)
        return observed

    async def _store_observed(
        self, record: ProbeRecord, observed: ObservedProbeState
    ) -> None:
        row = await self._db.scalar(
            select(ProbeObservedState).where(ProbeObservedState.probe_id == record.id)
        )
        if row is None:
            row = ProbeObservedState(
                probe_id=record.id, observed_at=observed.observed_at
            )
            self._db.add(row)
        row.observed_at = observed.observed_at
        row.reachable = observed.reachable
        row.document = observed.to_document()
        row.error_code = observed.error_code
        row.error_details = observed.error_details

    # --- Desired state ------------------------------------------------------

    async def _desired_for(self, record: ProbeRecord) -> DesiredProbeState:
        row = await self._db.scalar(
            select(ProbeDesiredState).where(
                ProbeDesiredState.probe_id == record.id,
                ProbeDesiredState.is_current.is_(True),
            )
        )
        if row is None:
            # No explicit intent yet. The sensors the shell tooling recorded in
            # runtime/probes/USER.sensors are the closest honest answer.
            inventory = self._runtime.read_probe(record.nats_username)
            return DesiredProbeState.from_document(
                {
                    "sensors": [{"name": name} for name in inventory.assigned_sensors],
                    "probe_name": inventory.probe_name,
                }
            )
        return DesiredProbeState.from_document(row.document)

    async def desired_document(self, record: ProbeRecord) -> dict[str, Any]:
        """The current desired state as a document, for the reconcile job."""
        desired = await self._desired_for(record)
        return desired.to_document()

    async def plan_reconciliation(
        self, probe_id: str, *, expected_ca_sha256: str | None
    ) -> ReconciliationPlan:
        detail = await self.get_detail(
            probe_id, connected_users=frozenset(), expected_ca_sha256=expected_ca_sha256
        )
        return build_plan(detail.record.nats_username, list(detail.deviations))

    # --- Helpers ------------------------------------------------------------

    def _observed_from_row(
        self, nats_username: str, row: ProbeObservedState | None
    ) -> ObservedProbeState | None:
        if row is None:
            return None
        return ObservedProbeState.from_document(
            nats_username=nats_username,
            observed_at=row.observed_at,
            reachable=row.reachable,
            document=row.document,
            error_code=row.error_code,
            error_details=row.error_details,
        )

    async def _active_job_ids(self) -> dict[str, str]:
        """Which probes currently have a job holding their lock."""
        rows = await self._db.execute(
            select(ResourceLock.resource_id, ResourceLock.job_id)
            .join(Job, Job.id == ResourceLock.job_id)
            .where(ResourceLock.resource_type == "probe")
        )
        return {str(resource_id): str(job_id) for resource_id, job_id in rows.all()}

    def _summarise(
        self,
        *,
        record: ProbeRecord,
        inventory: ProbeInventory,
        observed: ObservedProbeState | None,
        connected_users: frozenset[str],
        expected_ca_sha256: str | None,
        deviation_count: int,
        running_job_id: str | None,
    ) -> ProbeSummary:
        stale_after = timedelta(
            seconds=self._settings.observed_state_stale_after_seconds
        )
        stale = (
            observed is None or datetime.now(UTC) - observed.observed_at > stale_after
        )
        ca_state = (
            observed.ca_state(expected_ca_sha256)
            if observed is not None
            else CaState.UNKNOWN
        )
        nats_state = (
            NatsConnectionState.CONNECTED
            if inventory.nats_username in connected_users
            else NatsConnectionState.DISCONNECTED
            if connected_users
            else NatsConnectionState.UNKNOWN
        )
        return ProbeSummary(
            id=record.id,
            nats_username=inventory.nats_username,
            display_name=record.display_name,
            host=inventory.ssh_host,
            probe_name=(observed.probe_name if observed else None)
            or inventory.probe_name,
            status=_derive_status(
                record, observed, ca_state, nats_state, deviation_count
            ),
            service=observed.service if observed else ServiceState.UNKNOWN,
            package_version=observed.package_version if observed else None,
            ca_state=ca_state,
            nats_connection=nats_state,
            sensor_count=len(observed.sensors) if observed else 0,
            deviation_count=deviation_count,
            observed_at=observed.observed_at if observed else None,
            stale=stale,
            running_job_id=running_job_id,
            error_code=observed.error_code if observed else None,
            helper_version=observed.helper_version if observed else None,
            helper_outdated=observed.helper_outdated if observed else False,
        )


def _derive_status(
    record: ProbeRecord,
    observed: ObservedProbeState | None,
    ca_state: CaState,
    nats_state: NatsConnectionState,
    deviation_count: int,
) -> ProbeStatus:
    """One badge from several signals.

    Deliberately pessimistic: anything short of "everything checks out" is
    degraded, because a probe that looks fine but is not is the failure this
    platform exists to prevent.
    """
    if record.retired_at is not None:
        return ProbeStatus.RETIRED
    if observed is None:
        return ProbeStatus.PENDING
    if not observed.reachable:
        return ProbeStatus.UNREACHABLE
    if observed.service is not ServiceState.ACTIVE:
        return ProbeStatus.DEGRADED
    if ca_state in {CaState.MISSING, CaState.MISMATCHED}:
        return ProbeStatus.DEGRADED
    if nats_state is NatsConnectionState.DISCONNECTED:
        return ProbeStatus.DEGRADED
    if deviation_count:
        return ProbeStatus.DEGRADED
    if observed.probe_id is None:
        return ProbeStatus.ENROLLED
    return ProbeStatus.HEALTHY


def _with_sensors(
    observed: ObservedProbeState, sensors: tuple[InstalledSensor, ...]
) -> ObservedProbeState:
    """One field replaced, every other one carried over untouched.

    Listing the fields by hand here dropped helper_version and helper_sha256
    on every probe whose sensor list could be read - which is every healthy
    one. The platform then saw a probe reporting no helper version at all,
    called it older than itself, and told the operator to enrol it again;
    enrolling again produced the same probe and the same message.
    """
    return dataclasses.replace(observed, sensors=sensors)
