"""Turning measurements into a history, and reading availability back out.

This is the part of the availability monitoring that has to be right. The
interface only ever shows what these intervals say, so a fold that drops a
transition, counts an outage twice, or lets a single lost packet look like a
switched-off printer is wrong everywhere at once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import WatchCheckMethod, WatchState
from app.domain.watch import (
    PROTOCOL_VERSION,
    WatchProtocolError,
    WatchReport,
    WatchResult,
    next_state,
)
from app.persistence.models.inventory import ProbeRecord
from app.persistence.models.watch import (
    WatchDevice,
    WatchLatencyBucket,
    WatchObservation,
    WatchStateInterval,
)
from app.services.watch import SILENT_REASON, WatchService

ACCOUNT = "mpp-hamburg-01"
START = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


async def _fixture(db: AsyncSession, *, threshold: int = 3) -> WatchDevice:
    probe = ProbeRecord(nats_username=ACCOUNT, display_name="Hamburg")
    db.add(probe)
    await db.flush()
    device = WatchDevice(
        probe_id=probe.id,
        display_name="Kassendrucker 1",
        address="10.10.0.31",
        method=WatchCheckMethod.ICMP,
        labels={"team": "support", "site": "hamburg"},
        failure_threshold=threshold,
    )
    db.add(device)
    await db.flush()
    return device


def _report(device_id: str, *samples: tuple[int, bool]) -> WatchReport:
    """A report of (minute offset, reachable) pairs."""
    return WatchReport(
        account=ACCOUNT,
        sent_at=START + timedelta(minutes=samples[-1][0]),
        results=tuple(
            WatchResult(
                device_id=device_id,
                measured_at=START + timedelta(minutes=minute),
                reachable=reachable,
                rtt_ms=1.5 if reachable else None,
            )
            for minute, reachable in samples
        ),
    )


async def _intervals(db: AsyncSession, device_id: str) -> list[WatchStateInterval]:
    return list(
        (
            await db.scalars(
                select(WatchStateInterval)
                .where(WatchStateInterval.device_id == device_id)
                .order_by(WatchStateInterval.started_at)
            )
        ).all()
    )


class TestTheStateRule:
    """The threshold, in isolation from any database."""

    def test_one_lost_packet_does_not_make_an_outage(self) -> None:
        decision = next_state(
            current=WatchState.UP,
            consecutive_failures=0,
            reachable=False,
            failure_threshold=3,
        )
        assert decision.state is WatchState.UP
        assert decision.consecutive_failures == 1

    def test_the_threshold_is_what_flips_it(self) -> None:
        state = WatchState.UP
        failures = 0
        for _ in range(3):
            decision = next_state(
                current=state,
                consecutive_failures=failures,
                reachable=False,
                failure_threshold=3,
            )
            state, failures = decision.state, decision.consecutive_failures
        assert state is WatchState.DOWN

    def test_one_answer_clears_the_count(self) -> None:
        decision = next_state(
            current=WatchState.UP,
            consecutive_failures=2,
            reachable=True,
            failure_threshold=3,
        )
        assert decision.state is WatchState.UP
        assert decision.consecutive_failures == 0

    def test_an_unmeasured_device_that_answers_is_up_at_once(self) -> None:
        decision = next_state(
            current=WatchState.UNKNOWN,
            consecutive_failures=0,
            reachable=True,
            failure_threshold=3,
        )
        assert decision.state is WatchState.UP

    def test_an_unmeasured_device_that_stays_silent_is_not_yet_down(self) -> None:
        """UNKNOWN must not become UP just because something answered badly."""
        decision = next_state(
            current=WatchState.UNKNOWN,
            consecutive_failures=0,
            reachable=False,
            failure_threshold=3,
        )
        assert decision.state is WatchState.UNKNOWN

    def test_a_threshold_of_zero_still_needs_one_failure(self) -> None:
        decision = next_state(
            current=WatchState.UP,
            consecutive_failures=0,
            reachable=False,
            failure_threshold=0,
        )
        assert decision.state is WatchState.DOWN


class TestTheFold:
    async def test_a_steady_device_stays_one_row(self, db: AsyncSession) -> None:
        """Sixty measurements, one interval. The whole point of ADR 0011."""
        device = await _fixture(db)
        await WatchService(db).ingest(
            _report(device.id, *[(minute, True) for minute in range(60)])
        )

        intervals = await _intervals(db, device.id)
        assert len(intervals) == 1
        assert intervals[0].state is WatchState.UP
        assert intervals[0].samples == 60
        assert intervals[0].ended_at is None

    async def test_an_outage_closes_one_interval_and_opens_the_next(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db, threshold=2)
        await WatchService(db).ingest(
            _report(
                device.id,
                (0, True),
                (1, True),
                (2, False),
                (3, False),  # second failure: now it is down
                (4, False),
                (5, True),  # back up
            )
        )

        intervals = await _intervals(db, device.id)
        assert [interval.state for interval in intervals] == [
            WatchState.UP,
            WatchState.DOWN,
            WatchState.UP,
        ]
        # The outage is dated from the measurement that crossed the threshold,
        # not from the first missed packet: that is when it was established.
        assert intervals[1].started_at == START + timedelta(minutes=3)
        assert intervals[0].ended_at == START + timedelta(minutes=3)
        assert intervals[2].started_at == START + timedelta(minutes=5)

    async def test_a_flapping_packet_produces_no_interval_at_all(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db, threshold=3)
        await WatchService(db).ingest(
            _report(
                device.id,
                (0, True),
                (1, False),
                (2, True),
                (3, False),
                (4, True),
            )
        )

        intervals = await _intervals(db, device.id)
        assert len(intervals) == 1
        assert intervals[0].state is WatchState.UP
        # The failures are not lost - they are recorded inside the up interval,
        # which is what makes "up, but answering badly" visible afterwards.
        assert intervals[0].failures == 2
        assert intervals[0].samples == 5

    async def test_replaying_a_report_changes_nothing(self, db: AsyncSession) -> None:
        """The sensor re-sends what it could not deliver. That must be free."""
        device = await _fixture(db)
        report = _report(device.id, (0, True), (1, True), (2, True))
        service = WatchService(db)
        await service.ingest(report)
        second = await service.ingest(report)

        assert second.accepted == 0
        assert second.duplicates == 3
        intervals = await _intervals(db, device.id)
        assert len(intervals) == 1
        assert intervals[0].samples == 3

    async def test_a_report_for_another_probes_device_is_dropped(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db)
        other = ProbeRecord(nats_username="mpp-berlin-01")
        db.add(other)
        await db.flush()

        stranger = _report(device.id, (0, True))
        outcome = await WatchService(db).ingest(
            WatchReport(
                account="mpp-berlin-01",
                sent_at=stranger.sent_at,
                results=stranger.results,
            )
        )

        assert outcome.accepted == 0
        assert outcome.rejected == 1
        assert await _intervals(db, device.id) == []

    async def test_the_last_measurement_is_kept_flat(self, db: AsyncSession) -> None:
        device = await _fixture(db, threshold=1)
        await WatchService(db).ingest(
            WatchReport(
                account=ACCOUNT,
                sent_at=START,
                results=(
                    WatchResult(
                        device_id=device.id,
                        measured_at=START,
                        reachable=False,
                        error="no route to host",
                        resolved_address="10.10.0.31",
                    ),
                ),
            )
        )

        observation = await db.scalar(
            select(WatchObservation).where(WatchObservation.device_id == device.id)
        )
        assert observation is not None
        assert observation.state is WatchState.DOWN
        assert observation.error == "no route to host"
        assert observation.resolved_address == "10.10.0.31"

    async def test_latency_is_summarised_per_five_minutes(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db)
        await WatchService(db).ingest(
            WatchReport(
                account=ACCOUNT,
                sent_at=START,
                results=tuple(
                    WatchResult(
                        device_id=device.id,
                        measured_at=START + timedelta(minutes=minute),
                        reachable=True,
                        rtt_ms=rtt,
                    )
                    for minute, rtt in enumerate([1.0, 3.0, 2.0, 9.0, 4.0, 5.0])
                ),
            )
        )

        buckets = list(
            (
                await db.scalars(
                    select(WatchLatencyBucket)
                    .where(WatchLatencyBucket.device_id == device.id)
                    .order_by(WatchLatencyBucket.bucket_start)
                )
            ).all()
        )
        assert len(buckets) == 2
        assert buckets[0].samples == 5
        assert buckets[0].min_ms == 1.0
        assert buckets[0].max_ms == 9.0
        assert buckets[0].total_ms == 19.0
        assert buckets[1].samples == 1


class TestTheSilenceReaper:
    async def test_a_probe_that_stops_reporting_makes_its_devices_unknown(
        self, db: AsyncSession
    ) -> None:
        """A branch office losing its uplink is not every printer switching off."""
        device = await _fixture(db)
        service = WatchService(db)
        await service.ingest(_report(device.id, (0, True)))

        marked = await service.mark_silent_devices(
            now=START + timedelta(minutes=30), stale_after=timedelta(minutes=5)
        )

        assert marked == 1
        intervals = await _intervals(db, device.id)
        assert [interval.state for interval in intervals] == [
            WatchState.UP,
            WatchState.UNKNOWN,
        ]
        # Backdated to the last measurement: the device was up until then, and
        # claiming otherwise would invent an outage nobody observed.
        assert intervals[0].ended_at == START
        assert intervals[0].reason == SILENT_REASON

    async def test_a_reporting_probe_is_left_alone(self, db: AsyncSession) -> None:
        device = await _fixture(db)
        service = WatchService(db)
        await service.ingest(_report(device.id, (0, True)))

        marked = await service.mark_silent_devices(
            now=START + timedelta(minutes=2), stale_after=timedelta(minutes=5)
        )

        assert marked == 0

    async def test_marking_twice_does_not_stack_intervals(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db)
        service = WatchService(db)
        await service.ingest(_report(device.id, (0, True)))
        for _ in range(3):
            await service.mark_silent_devices(
                now=START + timedelta(hours=1), stale_after=timedelta(minutes=5)
            )

        intervals = await _intervals(db, device.id)
        assert len(intervals) == 2


class TestAvailability:
    async def test_uptime_is_computed_from_the_intervals(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db, threshold=1)
        service = WatchService(db)
        # Up for ten minutes, down for two, up again.
        await service.ingest(
            _report(
                device.id,
                *[(minute, True) for minute in range(10)],
                (10, False),
                (11, False),
                (12, True),
            )
        )

        summary = await service.availability(
            device.id, since=START, until=START + timedelta(minutes=12)
        )
        assert summary.down_seconds == 120
        assert summary.up_seconds == 600
        assert summary.outages == 1
        assert summary.longest_outage_seconds == 120
        assert summary.ratio == pytest.approx(600 / 720)

    async def test_unmeasured_time_is_not_counted_as_uptime(
        self, db: AsyncSession
    ) -> None:
        """The number that would otherwise lie in exactly the wrong moment."""
        device = await _fixture(db)
        service = WatchService(db)
        await service.ingest(_report(device.id, (0, True), (5, True)))
        await service.mark_silent_devices(
            now=START + timedelta(minutes=30), stale_after=timedelta(minutes=5)
        )

        summary = await service.availability(
            device.id, since=START, until=START + timedelta(minutes=30)
        )
        assert summary.unknown_seconds == pytest.approx(25 * 60)
        # 100%, honestly: of the time anybody measured, it was up throughout.
        assert summary.ratio == pytest.approx(1.0)

    async def test_a_device_nobody_measured_has_no_percentage(
        self, db: AsyncSession
    ) -> None:
        device = await _fixture(db)
        summary = await WatchService(db).availability(
            device.id, since=START, until=START + timedelta(hours=1)
        )
        assert summary.ratio is None

    async def test_outages_are_listed_newest_first(self, db: AsyncSession) -> None:
        device = await _fixture(db, threshold=1)
        service = WatchService(db)
        await service.ingest(
            _report(
                device.id,
                (0, True),
                (1, False),
                (2, True),
                (3, False),
                (4, True),
            )
        )

        outages = await service.outages(since=START)
        assert len(outages) == 2
        assert outages[0].started_at > outages[1].started_at


class TestTheWireFormat:
    def test_a_report_survives_the_round_trip(self) -> None:
        report = WatchReport(
            account=ACCOUNT,
            sent_at=START,
            results=(
                WatchResult(
                    device_id="01JABCDEF",
                    measured_at=START,
                    reachable=True,
                    rtt_ms=2.5,
                    resolved_address="10.10.0.31",
                ),
            ),
        )
        import json

        restored = WatchReport.from_wire(json.dumps(report.to_wire()).encode())
        assert restored == report

    def test_results_arrive_in_time_order(self) -> None:
        """Whatever order a probe sends its buffer in, the fold sees a timeline."""
        import json

        payload = {
            "version": PROTOCOL_VERSION,
            "account": ACCOUNT,
            "sent_at": START.isoformat(),
            "results": [
                {
                    "device_id": "d",
                    "at": (START + timedelta(minutes=5)).isoformat(),
                    "ok": True,
                },
                {"device_id": "d", "at": START.isoformat(), "ok": False},
            ],
        }
        report = WatchReport.from_wire(json.dumps(payload).encode())
        assert [result.measured_at for result in report.results] == [
            START,
            START + timedelta(minutes=5),
        ]

    def test_a_report_from_an_unknown_version_is_refused(self) -> None:
        import json

        payload = {"version": 99, "account": ACCOUNT, "sent_at": START.isoformat()}
        with pytest.raises(WatchProtocolError):
            WatchReport.from_wire(json.dumps(payload).encode())

    def test_an_oversized_report_is_refused_before_it_is_parsed(self) -> None:
        with pytest.raises(WatchProtocolError, match="exceeds"):
            WatchReport.from_wire(b"x" * (512 * 1024 + 1))

    def test_rubbish_is_refused_rather_than_crashing_the_subscription(self) -> None:
        with pytest.raises(WatchProtocolError):
            WatchReport.from_wire(b"not json at all")


class TestTargets:
    async def test_a_probe_is_told_only_its_own_devices(self, db: AsyncSession) -> None:
        device = await _fixture(db)
        other = ProbeRecord(nats_username="mpp-berlin-01")
        db.add(other)
        await db.flush()
        db.add(
            WatchDevice(
                probe_id=other.id,
                display_name="Berlin printer",
                address="10.20.0.9",
                method=WatchCheckMethod.ICMP,
            )
        )
        await db.flush()

        answer = await WatchService(db).targets_for_account(ACCOUNT)
        assert [target.device_id for target in answer.targets] == [device.id]

    async def test_an_unchanged_list_is_answered_short(self, db: AsyncSession) -> None:
        await _fixture(db)
        service = WatchService(db)
        first = await service.targets_for_account(ACCOUNT)
        again = await service.targets_for_account(
            ACCOUNT, known_revision=first.revision
        )

        assert again.unchanged is True
        assert again.targets == ()

    async def test_a_new_address_changes_the_revision(self, db: AsyncSession) -> None:
        device = await _fixture(db)
        service = WatchService(db)
        before = await service.targets_for_account(ACCOUNT)
        device.address = "10.10.0.99"
        await db.flush()
        after = await service.targets_for_account(ACCOUNT)

        assert before.revision != after.revision

    async def test_renaming_a_device_does_not(self, db: AsyncSession) -> None:
        """The revision covers what is measured, not what it is called."""
        device = await _fixture(db)
        service = WatchService(db)
        before = await service.targets_for_account(ACCOUNT)
        device.display_name = "Kassendrucker Eingang"
        device.labels = {"team": "kasse"}
        await db.flush()
        after = await service.targets_for_account(ACCOUNT)

        assert before.revision == after.revision

    async def test_a_disabled_device_is_not_measured(self, db: AsyncSession) -> None:
        device = await _fixture(db)
        device.enabled = False
        await db.flush()

        answer = await WatchService(db).targets_for_account(ACCOUNT)
        assert answer.targets == ()
