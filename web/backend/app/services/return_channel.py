"""The state of each probe's return channel (ADR 0013, issue #176).

Observed state per ADR 0002: written by every delivery a probe makes, read by
the probe's page, the alert derivation and the automatic repair. Besides the
records a sensor wrote, the service on the probe sends an empty batch at
least every HEARTBEAT_INTERVAL, so a working channel is never quiet for long
whether or not a sensor writes to the log.

A channel is silent once nothing arrived for the silence threshold, counted
from its last delivery or from when its certificate was issued, whichever is
later: a probe that got a certificate and never delivered is as broken as
one that stopped. The issue time comes from probe-info of helper 13 and
later, the version that brought the heartbeat; an older probe that never
delivered may simply have no sensor that writes to the log, and is left
alone.
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
from app.services.sensor_logs import parse_time

# What the service on the probe calls itself in the gap records it sends.
GAP_SENSOR = "return-channel"

# How often the service on the probe sends a heartbeat when it has nothing
# else to deliver - HEARTBEAT_SECONDS in its channel.env, this by default.
# The silence threshold defaults to three of these.
HEARTBEAT_INTERVAL = timedelta(minutes=5)
# How long a running service may stay quiet before the repair blames the
# certificate: one heartbeat and a minute for it to arrive.
HEARTBEAT_WINDOW = HEARTBEAT_INTERVAL + timedelta(minutes=1)


def silence_threshold(settings: Settings) -> timedelta:
    return timedelta(minutes=settings.probe_channel_silence_minutes)


def last_sign_of_life(
    last_contact_at: datetime | None, certificate_issued_at: datetime | None
) -> datetime | None:
    """Since when the channel should have delivered, or None if unknown."""
    known = [moment for moment in (last_contact_at, certificate_issued_at) if moment]
    return max(known) if known else None


def is_silent(since: datetime | None, settings: Settings, now: datetime) -> bool:
    return since is not None and now - since > silence_threshold(settings)


def needs_new_certificate(
    *,
    certificate: str | None,
    service: str | None,
    since: datetime | None,
    now: datetime,
) -> bool:
    """Whether the repair has to issue a certificate rather than refresh
    the service.

    A missing or expired certificate, of course. Beyond that only a running
    service that let a heartbeat window pass without a delivery: the proxy
    refuses a bad certificate before the backend sees it, so that is the
    one way a rejected certificate shows. A probe that delivers keeps its
    certificate, however often a repair is started.
    """
    if not certificate_usable(certificate, now.date()):
        return True
    if service != "active":
        return False
    return since is None or now - since > HEARTBEAT_WINDOW


def certificate_usable(expires: str | None, today: date) -> bool:
    """Whether the date probe-info reported still names a valid certificate."""
    if not expires:
        return False
    try:
        return date.fromisoformat(expires) > today
    except ValueError:
        return False


async def last_contact(session: AsyncSession, nats_username: str) -> datetime | None:
    """When the probe last delivered anything, heartbeats included."""
    contact: datetime | None = await session.scalar(
        select(ProbeReturnChannel.last_contact_at)
        .join(ProbeRecord, ProbeRecord.id == ProbeReturnChannel.probe_id)
        .where(ProbeRecord.nats_username == nats_username)
    )
    return contact


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
        moment
        for entry in stored
        if entry.get("result") == "gap" and entry.get("sensor") == GAP_SENSOR
        if (moment := parse_time(entry.get("time"))) is not None
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
    today = now.date()
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
        issued = parse_time(info.channel_cert_issued) if info is not None else None
        last_contact = row.last_contact_at if row is not None else None
        states[probe_id] = ReturnChannelState(
            last_contact_at=last_contact,
            buffer_bytes=row.buffer_bytes if row is not None else None,
            buffer_limit=row.buffer_limit if row is not None else None,
            gap_count=row.gap_count if row is not None else 0,
            last_gap_at=row.last_gap_at if row is not None else None,
            observed_at=row.observed_at if row is not None else None,
            certificate_expires=certificate,
            certificate_issued_at=issued,
            certificate_expired=certificate is not None
            and not certificate_usable(certificate, today),
            service=service,
            silent=is_silent(last_sign_of_life(last_contact, issued), settings, now),
        )
    return states
