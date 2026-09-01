"""Availability monitoring: the devices, their history, and the ingest.

The ingest is the interesting half. Every measurement either extends the
open interval or closes it and opens the next one, which is what keeps a
year of a printer being switched on at a handful of rows instead of half a
million samples - see ADR 0011.

Everything here is deliberately ordinary SQL against the session it is
handed. There is no queue and no batching layer: a report from one probe is
a few dozen rows, and the platform runs one process (ADR 0004).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.domain.enums import WatchCheckMethod, WatchState
from app.domain.watch import (
    WatchReport,
    WatchResult,
    WatchTarget,
    WatchTargetList,
    latency_bucket_start,
    next_state,
)
from app.persistence.models.inventory import ProbeRecord
from app.persistence.models.watch import (
    WatchDevice,
    WatchLatencyBucket,
    WatchObservation,
    WatchStateInterval,
)

logger = get_logger(__name__)

# How long a device may go unmeasured before its state stops being a
# statement about the device and becomes one about the measurement. Three
# minutes of a one-minute scanning interval is two missed runs, which is
# late enough not to fire on a single slow scan.
DEFAULT_STALE_AFTER = timedelta(seconds=300)

# Written into the interval that the reaper closes, so the interface can say
# why a device went quiet instead of implying it went dark.
SILENT_REASON = "probe stopped reporting"


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """What one report did, for the log and for the tests."""

    accepted: int = 0
    # Results for devices this account does not own, or does not have at all.
    # Counted rather than raised: one stale device id must not cost the other
    # forty-nine measurements in the same report.
    rejected: int = 0
    # Results that were already in the history. The sensor re-sends what it
    # could not deliver, so these are the normal cost of that guarantee and
    # not a problem - they are counted apart from accepted so that "the probe
    # is only ever re-sending" is visible rather than looking like traffic.
    duplicates: int = 0
    transitions: int = 0


@dataclass(frozen=True, slots=True)
class _Applied:
    """What one measurement did to one device."""

    applied: bool
    changed: bool = False


class WatchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Targets ------------------------------------------------------------

    async def targets_for_account(
        self, account: str, *, known_revision: str = ""
    ) -> WatchTargetList:
        """What the probe behind ``account`` should measure.

        Answers ``unchanged`` when the probe already holds this revision. The
        revision is derived from the devices themselves rather than kept in a
        column, so nothing can forget to bump it.
        """
        devices = (
            await self._session.scalars(
                select(WatchDevice)
                .join(ProbeRecord, WatchDevice.probe_id == ProbeRecord.id)
                .where(
                    ProbeRecord.nats_username == account,
                    WatchDevice.enabled.is_(True),
                )
                .order_by(WatchDevice.id)
            )
        ).all()

        revision = _revision_of(devices)
        if known_revision and known_revision == revision:
            return WatchTargetList(revision=revision, targets=(), unchanged=True)

        return WatchTargetList(
            revision=revision,
            targets=tuple(
                WatchTarget(
                    device_id=device.id,
                    address=device.address,
                    method=device.method,
                    port=device.port,
                )
                for device in devices
            ),
            stale_after_seconds=int(DEFAULT_STALE_AFTER.total_seconds()),
        )

    # --- Ingest -------------------------------------------------------------

    async def ingest(self, report: WatchReport) -> IngestOutcome:
        """Fold one probe's report into the history.

        The account in the subject decides what may be written. A report may
        only touch devices assigned to the probe that sent it - see the third
        cost in ADR 0011 for why that check is here and not in the NATS
        permissions.
        """
        if not report.results:
            return IngestOutcome()

        owned = {
            device.id: device
            for device in (
                await self._session.scalars(
                    select(WatchDevice)
                    .join(ProbeRecord, WatchDevice.probe_id == ProbeRecord.id)
                    .where(
                        ProbeRecord.nats_username == report.account,
                        WatchDevice.id.in_(
                            {result.device_id for result in report.results}
                        ),
                    )
                )
            ).all()
        }

        accepted = 0
        rejected = 0
        duplicates = 0
        transitions = 0
        # Sorted by time in from_wire, so replaying a buffered run produces
        # the same history it would have produced when it happened.
        for result in report.results:
            device = owned.get(result.device_id)
            if device is None:
                rejected += 1
                continue
            outcome = await self._apply(device, result)
            if not outcome.applied:
                duplicates += 1
                continue
            accepted += 1
            transitions += int(outcome.changed)

        if rejected:
            logger.warning(
                "report named devices this probe does not own",
                extra={
                    "probe": report.account,
                    "rejected": rejected,
                    "accepted": accepted,
                },
            )
        return IngestOutcome(
            accepted=accepted,
            rejected=rejected,
            duplicates=duplicates,
            transitions=transitions,
        )

    async def _apply(self, device: WatchDevice, result: WatchResult) -> _Applied:
        """Fold one measurement into one device's history."""
        observation = await self._session.scalar(
            select(WatchObservation).where(WatchObservation.device_id == device.id)
        )
        if observation is not None and observation.observed_at >= result.measured_at:
            # A buffered result the probe re-sent after this one already
            # landed. Dropping it keeps the fold idempotent, which is what
            # lets the sensor re-send freely.
            return _Applied(applied=False)

        current = observation.state if observation else WatchState.UNKNOWN
        failures = observation.consecutive_failures if observation else 0
        decision = next_state(
            current=current,
            consecutive_failures=failures,
            reachable=result.reachable,
            failure_threshold=device.failure_threshold,
        )

        if observation is None:
            observation = WatchObservation(device_id=device.id, state=decision.state)
            self._session.add(observation)
        observation.observed_at = result.measured_at
        observation.state = decision.state
        observation.consecutive_failures = decision.consecutive_failures
        observation.rtt_ms = result.rtt_ms
        observation.resolved_address = result.resolved_address
        observation.error = result.error

        changed = await self._extend_or_open(
            device_id=device.id,
            state=decision.state,
            moment=result.measured_at,
            failed=not result.reachable,
            reason=result.error,
        )
        if result.rtt_ms is not None:
            await self._record_latency(device.id, result.measured_at, result.rtt_ms)
        return _Applied(applied=True, changed=changed)

    async def _extend_or_open(
        self,
        *,
        device_id: str,
        state: WatchState,
        moment: datetime,
        failed: bool,
        reason: str | None,
    ) -> bool:
        open_interval = await self._open_interval(device_id)

        if open_interval is not None and open_interval.state is state:
            open_interval.samples += 1
            open_interval.failures += int(failed)
            return False

        if open_interval is not None:
            open_interval.ended_at = moment
            open_interval.reason = reason

        self._session.add(
            WatchStateInterval(
                device_id=device_id,
                state=state,
                started_at=moment,
                samples=1,
                failures=int(failed),
            )
        )
        return open_interval is not None

    async def _open_interval(self, device_id: str) -> WatchStateInterval | None:
        interval: WatchStateInterval | None = await self._session.scalar(
            select(WatchStateInterval).where(
                WatchStateInterval.device_id == device_id,
                WatchStateInterval.ended_at.is_(None),
            )
        )
        return interval

    async def _record_latency(
        self, device_id: str, moment: datetime, rtt_ms: float
    ) -> None:
        bucket_start = latency_bucket_start(moment)
        bucket = await self._session.scalar(
            select(WatchLatencyBucket).where(
                WatchLatencyBucket.device_id == device_id,
                WatchLatencyBucket.bucket_start == bucket_start,
            )
        )
        if bucket is None:
            self._session.add(
                WatchLatencyBucket(
                    device_id=device_id,
                    bucket_start=bucket_start,
                    samples=1,
                    min_ms=rtt_ms,
                    max_ms=rtt_ms,
                    total_ms=rtt_ms,
                )
            )
            return
        bucket.samples += 1
        bucket.min_ms = min(bucket.min_ms, rtt_ms)
        bucket.max_ms = max(bucket.max_ms, rtt_ms)
        bucket.total_ms += rtt_ms

    # --- The reaper ---------------------------------------------------------

    async def mark_silent_devices(
        self, *, now: datetime | None = None, stale_after: timedelta | None = None
    ) -> int:
        """Turn "nobody measured" into ``UNKNOWN`` rather than leaving a lie.

        A branch office that loses its uplink takes its probe with it. Without
        this, every device behind it would keep the state it had when the
        line went down - a wall of green that has not been true for hours,
        which is worse than no answer at all.
        """
        now = now or datetime.now(UTC)
        cutoff = now - (stale_after or DEFAULT_STALE_AFTER)

        stale = (
            await self._session.scalars(
                select(WatchObservation)
                .join(WatchDevice, WatchObservation.device_id == WatchDevice.id)
                .where(
                    WatchObservation.observed_at < cutoff,
                    WatchObservation.state != WatchState.UNKNOWN,
                    WatchDevice.enabled.is_(True),
                )
            )
        ).all()

        for observation in stale:
            observation.state = WatchState.UNKNOWN
            observation.consecutive_failures = 0
            open_interval = await self._open_interval(observation.device_id)
            if open_interval is not None and open_interval.state is WatchState.UNKNOWN:
                continue
            if open_interval is not None:
                # Ends where the measuring stopped, not where somebody
                # noticed: the device was measured up to that point, and
                # backdating is the only honest option.
                open_interval.ended_at = observation.observed_at
                open_interval.reason = SILENT_REASON
            self._session.add(
                WatchStateInterval(
                    device_id=observation.device_id,
                    state=WatchState.UNKNOWN,
                    started_at=observation.observed_at,
                    samples=0,
                    failures=0,
                    reason=SILENT_REASON,
                )
            )

        if stale:
            logger.info("devices went unmeasured", extra={"count": len(stale)})
        return len(stale)

    # --- Reading ------------------------------------------------------------

    async def availability(
        self, device_id: str, *, since: datetime, until: datetime | None = None
    ) -> AvailabilitySummary:
        """Exact uptime over a window, from the intervals themselves.

        Every interval is clipped to the window and its seconds are added to
        its state's total. Time nobody measured stays its own number instead
        of being counted as either up or down, because a percentage that
        silently includes an outage of the measurement is a percentage that
        lies in exactly the situation somebody is asking about.
        """
        until = until or datetime.now(UTC)
        if until <= since:
            raise ValidationFailedError(details="the window ends before it starts")

        intervals = (
            await self._session.scalars(
                select(WatchStateInterval).where(
                    WatchStateInterval.device_id == device_id,
                    WatchStateInterval.started_at < until,
                    (WatchStateInterval.ended_at.is_(None))
                    | (WatchStateInterval.ended_at > since),
                )
            )
        ).all()

        seconds = dict.fromkeys(WatchState, 0.0)
        outages = 0
        longest = 0.0
        for interval in intervals:
            start = max(interval.started_at, since)
            end = min(interval.ended_at or until, until)
            if end <= start:
                continue
            span = (end - start).total_seconds()
            seconds[interval.state] += span
            if interval.state is WatchState.DOWN:
                outages += 1
                longest = max(longest, span)

        measured = seconds[WatchState.UP] + seconds[WatchState.DOWN]
        return AvailabilitySummary(
            device_id=device_id,
            since=since,
            until=until,
            up_seconds=seconds[WatchState.UP],
            down_seconds=seconds[WatchState.DOWN],
            unknown_seconds=seconds[WatchState.UNKNOWN],
            outages=outages,
            longest_outage_seconds=longest,
            ratio=(seconds[WatchState.UP] / measured) if measured else None,
        )

    async def outages(
        self, *, since: datetime, device_ids: list[str] | None = None, limit: int = 100
    ) -> list[WatchStateInterval]:
        """The down intervals, newest first - what support actually reads."""
        statement = (
            select(WatchStateInterval)
            .where(
                WatchStateInterval.state == WatchState.DOWN,
                (WatchStateInterval.ended_at.is_(None))
                | (WatchStateInterval.ended_at > since),
            )
            .order_by(WatchStateInterval.started_at.desc())
            .limit(limit)
        )
        if device_ids is not None:
            statement = statement.where(WatchStateInterval.device_id.in_(device_ids))
        return list((await self._session.scalars(statement)).all())

    async def list_devices(
        self, *, label_filter: dict[str, str] | None = None
    ) -> list[tuple[WatchDevice, WatchObservation | None]]:
        """Every device with its last measurement, filtered by labels.

        The filter is applied here rather than in SQL: labels are a JSON
        column, and matching a handful of keys over a few hundred rows in
        Python is both faster to run and far easier to keep correct than the
        equivalent JSON path expression on two dialects.
        """
        rows = (
            await self._session.execute(
                select(WatchDevice, WatchObservation)
                .outerjoin(
                    WatchObservation, WatchObservation.device_id == WatchDevice.id
                )
                .order_by(WatchDevice.display_name)
            )
        ).all()
        devices = [(row[0], row[1]) for row in rows]
        if not label_filter:
            return devices
        return [
            (device, observation)
            for device, observation in devices
            if all(
                device.labels.get(key) == value for key, value in label_filter.items()
            )
        ]

    async def label_values(self) -> dict[str, list[str]]:
        """Every label key with the values in use, for the filter menu."""
        devices = (await self._session.scalars(select(WatchDevice))).all()
        collected: dict[str, set[str]] = {}
        for device in devices:
            for key, value in device.labels.items():
                collected.setdefault(key, set()).add(value)
        return {key: sorted(values) for key, values in sorted(collected.items())}

    # --- Writing ------------------------------------------------------------

    async def create_device(
        self,
        *,
        probe_id: str,
        display_name: str,
        address: str,
        method: WatchCheckMethod,
        port: int | None,
        labels: dict[str, str],
        failure_threshold: int,
        notes: str | None,
    ) -> WatchDevice:
        probe = await self._session.get(ProbeRecord, probe_id)
        if probe is None:
            raise NotFoundError(details=f"no probe {probe_id}")
        _validate_check(method, port)

        device = WatchDevice(
            probe_id=probe_id,
            display_name=display_name,
            address=address,
            method=method,
            port=port,
            labels=labels,
            failure_threshold=failure_threshold,
            notes=notes,
        )
        self._session.add(device)
        await self._session.flush()
        return device

    async def get_device(self, device_id: str) -> WatchDevice:
        device = await self._session.get(WatchDevice, device_id)
        if device is None:
            raise NotFoundError(details=f"no watched device {device_id}")
        return device

    async def delete_device(self, device_id: str) -> None:
        device = await self.get_device(device_id)
        await self._session.delete(device)

    async def purge_history(self, *, before: datetime) -> int:
        """Drop closed intervals and latency buckets older than ``before``.

        Not called on a schedule. It exists because a retention decision is
        the operator's, and because the alternative - a table that only ever
        grows - is how a small feature becomes a large problem.
        """
        removed = 0
        for statement in (
            delete(WatchStateInterval).where(
                WatchStateInterval.ended_at.is_not(None),
                WatchStateInterval.ended_at < before,
            ),
            delete(WatchLatencyBucket).where(WatchLatencyBucket.bucket_start < before),
        ):
            result = await self._session.execute(statement)
            removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return removed

    async def count_devices(self) -> int:
        return int(
            await self._session.scalar(select(func.count()).select_from(WatchDevice))
            or 0
        )


@dataclass(frozen=True, slots=True)
class AvailabilitySummary:
    device_id: str
    since: datetime
    until: datetime
    up_seconds: float
    down_seconds: float
    unknown_seconds: float
    outages: int
    longest_outage_seconds: float
    # None when nothing was measured in the window at all. Deliberately not
    # 0.0: "we do not know" and "it was down the whole time" are different
    # answers, and only one of them warrants a phone call.
    ratio: float | None


def _validate_check(method: WatchCheckMethod, port: int | None) -> None:
    if method is WatchCheckMethod.TCP and (port is None or not 1 <= port <= 65535):
        raise ValidationFailedError(
            details="a TCP check needs a port between 1 and 65535"
        )


def _revision_of(devices: Sequence[WatchDevice]) -> str:
    """A fingerprint of the device list, stable across processes.

    Built from what the sensor actually measures, so editing a display name
    or a label does not send every probe a new list.
    """
    digest = hashlib.sha256()
    for device in devices:
        digest.update(
            f"{device.id}\t{device.address}\t{device.method}\t{device.port}\n".encode()
        )
    return digest.hexdigest()[:16]
