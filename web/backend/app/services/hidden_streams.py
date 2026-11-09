"""Timeline rows taken off a sensor's page on purpose.

A network configured by mistake stays on the timeline for as long as its runs
are in the window - a day, a week. Hiding it records the moment, and the row
stays away until a run newer than that arrives. Nothing here touches the
sensor log: the records stay in the runtime volume and in the log.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import HiddenSensorStream, ProbeRecord
from app.services.sensor_logs import TimelineStream


async def hidden_since(
    db: AsyncSession, probe: str, sensor: str | None = None
) -> dict[tuple[str, str], datetime]:
    """When each hidden row of a probe was hidden, by sensor and stream key."""
    query = (
        select(HiddenSensorStream)
        .join(ProbeRecord, ProbeRecord.id == HiddenSensorStream.probe_id)
        .where(ProbeRecord.nats_username == probe)
    )
    if sensor is not None:
        query = query.where(HiddenSensorStream.sensor == sensor)
    rows = (await db.execute(query)).scalars()
    return {(row.sensor, row.stream): row.hidden_at for row in rows}


def visible(
    streams: Iterable[TimelineStream], hidden: dict[tuple[str, str], datetime]
) -> list[TimelineStream]:
    """The rows still shown: never hidden, or run again since."""
    out = []
    for stream in streams:
        since = hidden.get((stream.sensor, stream.key))
        if since is None or (stream.last_run is not None and stream.last_run > since):
            out.append(stream)
    return out


async def hide(
    db: AsyncSession,
    probe: ProbeRecord,
    sensor: str,
    stream: str,
    *,
    now: datetime,
    by: str | None,
) -> tuple[HiddenSensorStream, datetime | None]:
    """Hide a row from now on, and say when it was hidden before, if ever.

    Hiding a row that came back moves the moment forward instead of adding a
    second entry: only the latest hide decides what is shown.
    """
    row = (
        await db.execute(
            select(HiddenSensorStream).where(
                HiddenSensorStream.probe_id == probe.id,
                HiddenSensorStream.sensor == sensor,
                HiddenSensorStream.stream == stream,
            )
        )
    ).scalar_one_or_none()
    previous = row.hidden_at if row is not None else None
    if row is None:
        row = HiddenSensorStream(probe_id=probe.id, sensor=sensor, stream=stream)
        db.add(row)
    row.hidden_at = now
    row.hidden_by = by
    await db.flush()
    return row, previous
