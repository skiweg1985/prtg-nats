"""The state of each probe's return channel (ADR 0013, issue #176).

Observed state per ADR 0002: written by every delivery a probe makes, read by
the probe's page, the alert derivation and the automatic repair. The channel
has no heartbeat - the service on the probe posts when a sensor wrote
something - so only a probe that has delivered before can fall silent. One
that never delivered may simply have no sensor that writes to the log, and
warning about it would put a permanent alert on every such probe.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import ObservedProbeState, ReturnChannelState
from app.persistence.models.inventory import ProbeRecord, ProbeReturnChannel

# What the service on the probe calls itself in the gap records it sends.
GAP_SENSOR = "return-channel"


def silence_threshold(settings: Settings) -> timedelta:
    return timedelta(minutes=settings.probe_channel_silence_minutes)


def is_silent(last_contact_at: datetime, settings: Settings, now: datetime) -> bool:
    return now - last_contact_at > silence_threshold(settings)


def certificate_usable(expires: str | None, today: date) -> bool:
    """Whether the date probe-info reported still names a valid certificate."""
    if not expires:
        return False
    try:
        return date.fromisoformat(expires) > today
    except ValueError:
        return False


async def record_delivery(
    session: AsyncSession,
    nats_username: str,
    *,
    buffer: tuple[int, int] | None,
    stored: Sequence[dict[str, Any]],
) -> None:
    """Note a batch from the probe: its time, its buffer and its new gaps.

    Only records stored for the first time are counted, so a batch sent
    again after a lost answer leaves the gap count as it was.
    """
    now = datetime.now(UTC)
    record = await session.scalar(
        select(ProbeRecord).where(ProbeRecord.nats_username == nats_username)
    )
    if record is None:
        # First sight of a probe the inventory names, the same as listing it.
        record = ProbeRecord(nats_username=nats_username)
        session.add(record)
        await session.flush()
    row = await session.scalar(
        select(ProbeReturnChannel).where(ProbeReturnChannel.probe_id == record.id)
    )
    if row is None:
        row = ProbeReturnChannel(probe_id=record.id, gap_count=0)
        session.add(row)

    gaps = [
        _parse_time(entry["time"])
        for entry in stored
        if entry.get("result") == "gap" and entry.get("sensor") == GAP_SENSOR
    ]
    row.last_contact_at = now
    row.observed_at = now
    if buffer is not None:
        row.buffer_bytes, row.buffer_limit = buffer
    if gaps:
        row.gap_count = (row.gap_count or 0) + len(gaps)
        latest = max(gaps)
        if row.last_gap_at is None or latest > row.last_gap_at:
            row.last_gap_at = latest


async def channel_states(
    session: AsyncSession,
    settings: Settings,
    probe_ids: Sequence[str],
    observed: dict[str, ObservedProbeState | None],
) -> dict[str, ReturnChannelState]:
    """The channel per probe id, for probes that have one.

    A probe has one when it has delivered, or when its last probe-info named
    a certificate. ``observed`` is keyed by probe id like the result.
    """
    now = datetime.now(UTC)
    rows = {
        row.probe_id: row
        for row in await session.scalars(
            select(ProbeReturnChannel).where(ProbeReturnChannel.probe_id.in_(probe_ids))
        )
    }
    states: dict[str, ReturnChannelState] = {}
    for probe_id in probe_ids:
        row = rows.get(probe_id)
        info = observed.get(probe_id)
        certificate = info.channel_cert if info is not None else None
        service = info.channel_service if info is not None else None
        if row is None and certificate is None:
            continue
        if row is None:
            states[probe_id] = ReturnChannelState(
                certificate_expires=certificate, service=service
            )
            continue
        states[probe_id] = ReturnChannelState(
            last_contact_at=row.last_contact_at,
            buffer_bytes=row.buffer_bytes,
            buffer_limit=row.buffer_limit,
            gap_count=row.gap_count,
            last_gap_at=row.last_gap_at,
            observed_at=row.observed_at,
            certificate_expires=certificate,
            service=service,
            silent=is_silent(row.last_contact_at, settings, now),
        )
    return states


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
