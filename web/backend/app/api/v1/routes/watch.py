"""Availability monitoring: the watch list, the dashboard, the history.

Reading is a separate permission from managing, which is the point of the
whole feature: the people who need the answer - a shop manager wondering
whether the till printer is on - get a viewer account and nothing else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    DbSession,
    PrincipalDep,
    require_permission,
)
from app.api.schemas.watch import (
    WatchAvailabilityOut,
    WatchDeviceIn,
    WatchDeviceOut,
    WatchDeviceUpdateIn,
    WatchOutageOut,
    WatchOverviewOut,
)
from app.core.errors import NotFoundError
from app.core.permissions import Permission
from app.domain.enums import WatchState
from app.persistence.models.inventory import ProbeRecord
from app.persistence.models.watch import WatchDevice, WatchObservation
from app.services.watch import DEFAULT_STALE_AFTER, WatchService

router = APIRouter(prefix="/watch", tags=["watch"])


def _out(
    device: WatchDevice,
    observation: WatchObservation | None,
    probe_names: dict[str, str],
    *,
    now: datetime,
) -> WatchDeviceOut:
    stale = observation is None or (now - observation.observed_at) > DEFAULT_STALE_AFTER
    return WatchDeviceOut(
        id=device.id,
        display_name=device.display_name,
        address=device.address,
        probe_id=device.probe_id,
        probe_name=probe_names.get(device.probe_id, device.probe_id),
        method=device.method,
        port=device.port,
        labels=device.labels,
        enabled=device.enabled,
        failure_threshold=device.failure_threshold,
        notes=device.notes,
        state=observation.state if observation else WatchState.UNKNOWN,
        observed_at=observation.observed_at if observation else None,
        rtt_ms=observation.rtt_ms if observation else None,
        error=observation.error if observation else None,
        stale=stale,
    )


async def _probe_names(db: DbSession) -> dict[str, str]:
    records = (await db.scalars(select(ProbeRecord))).all()
    return {
        record.id: record.display_name or record.nats_username for record in records
    }


def _label_filter(pairs: list[str]) -> dict[str, str]:
    """``?label=team:support&label=site:hamburg`` as a dictionary.

    A pair without a colon is ignored rather than rejected: the filter comes
    out of a URL somebody may have edited by hand, and dropping a malformed
    one shows more devices, never fewer than the caller may see.
    """
    parsed: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition(":")
        if separator and key:
            parsed[key] = value
    return parsed


@router.get("/overview", response_model=WatchOverviewOut)
async def overview(
    request: Request,
    db: DbSession,
    label: Annotated[list[str], Query()] = [],  # noqa: B006
    _: Annotated[object, Depends(require_permission(Permission.WATCH_READ))] = None,
) -> WatchOverviewOut:
    """Everything the dashboard shows, in one request.

    One query rather than one per device: a support desk leaves this page
    open on a wall display, and a page that costs three hundred requests a
    minute is a page somebody turns off.
    """
    service = WatchService(db)
    now = datetime.now(UTC)
    devices = await service.list_devices(label_filter=_label_filter(label))
    names = await _probe_names(db)

    rows = [
        _out(device, observation, names, now=now) for device, observation in devices
    ]
    # A stale row counts as unknown whatever it last said, so the three
    # numbers always add up to the devices being watched.
    counted = [row for row in rows if row.enabled]
    live = [row for row in counted if not row.stale]
    return WatchOverviewOut(
        devices=rows,
        up=sum(1 for row in live if row.state is WatchState.UP),
        down=sum(1 for row in live if row.state is WatchState.DOWN),
        unknown=sum(
            1 for row in counted if row.stale or row.state is WatchState.UNKNOWN
        ),
        labels=await service.label_values(),
        receiving=_receiving(request),
    )


def _receiving(request: Request) -> bool:
    """Whether the ingest holds a NATS connection.

    Read off the running worker rather than stored: it is a property of this
    process, and a value in the database would be a lie the moment the
    process changed its mind.
    """
    ingest = getattr(request.app.state, "watch_ingest", None)
    return bool(getattr(ingest, "connected", False))


@router.get("/devices", response_model=list[WatchDeviceOut])
async def list_devices(
    db: DbSession,
    label: Annotated[list[str], Query()] = [],  # noqa: B006
    _: Annotated[object, Depends(require_permission(Permission.WATCH_READ))] = None,
) -> list[WatchDeviceOut]:
    now = datetime.now(UTC)
    names = await _probe_names(db)
    devices = await WatchService(db).list_devices(label_filter=_label_filter(label))
    return [
        _out(device, observation, names, now=now) for device, observation in devices
    ]


@router.post(
    "/devices", response_model=WatchDeviceOut, status_code=status.HTTP_201_CREATED
)
async def create_device(
    payload: WatchDeviceIn,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_MANAGE))],
) -> WatchDeviceOut:
    device = await WatchService(db).create_device(
        probe_id=payload.probe_id,
        display_name=payload.display_name,
        address=payload.address,
        method=payload.method,
        port=payload.port,
        labels=payload.labels,
        failure_threshold=payload.failure_threshold,
        notes=payload.notes,
        enabled=payload.enabled,
    )
    audit.record(
        action="watch.device_create",
        object_type="watch_device",
        object_id=device.id,
        object_label=device.display_name,
        after={"address": device.address, "probe_id": device.probe_id},
    )
    return _out(device, None, await _probe_names(db), now=datetime.now(UTC))


@router.patch("/devices/{device_id}", response_model=WatchDeviceOut)
async def update_device(
    device_id: str,
    payload: WatchDeviceUpdateIn,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_MANAGE))],
) -> WatchDeviceOut:
    service = WatchService(db)
    device = await service.get_device(device_id)
    before = {"address": device.address, "probe_id": device.probe_id}

    fields = payload.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(device, field, value)
    # Re-validated after the change rather than before: switching a device to
    # TCP without naming a port has to fail here, not on the probe.
    await service.validate_device(device)
    await db.flush()

    audit.record(
        action="watch.device_update",
        object_type="watch_device",
        object_id=device.id,
        object_label=device.display_name,
        before=before,
        after={"address": device.address, "probe_id": device.probe_id},
    )
    observation = await db.scalar(
        select(WatchObservation).where(WatchObservation.device_id == device.id)
    )
    return _out(device, observation, await _probe_names(db), now=datetime.now(UTC))


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: str,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_MANAGE))],
) -> None:
    service = WatchService(db)
    device = await service.get_device(device_id)
    label = device.display_name
    await service.delete_device(device_id)
    audit.record(
        action="watch.device_delete",
        object_type="watch_device",
        object_id=device_id,
        object_label=label,
    )


@router.get("/devices/{device_id}/availability", response_model=WatchAvailabilityOut)
async def availability(
    device_id: str,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_READ))] = None,
) -> WatchAvailabilityOut:
    service = WatchService(db)
    await service.get_device(device_id)
    until = datetime.now(UTC)
    summary = await service.availability(
        device_id, since=until - timedelta(days=days), until=until
    )
    return WatchAvailabilityOut(
        device_id=summary.device_id,
        since=summary.since,
        until=summary.until,
        up_seconds=summary.up_seconds,
        down_seconds=summary.down_seconds,
        unknown_seconds=summary.unknown_seconds,
        outages=summary.outages,
        longest_outage_seconds=summary.longest_outage_seconds,
        ratio=summary.ratio,
    )


@router.get("/outages", response_model=list[WatchOutageOut])
async def outages(
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=366)] = 7,
    label: Annotated[list[str], Query()] = [],  # noqa: B006
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_READ))] = None,
) -> list[WatchOutageOut]:
    """The outage list, which is what support actually reads."""
    service = WatchService(db)
    since = datetime.now(UTC) - timedelta(days=days)

    filtered = _label_filter(label)
    devices = await service.list_devices(label_filter=filtered)
    names = {device.id: device.display_name for device, _ in devices}
    intervals = await service.outages(
        since=since,
        device_ids=list(names) if filtered else None,
        limit=limit,
    )

    rows = []
    for interval in intervals:
        name = names.get(interval.device_id)
        if name is None:
            # Filtered out, or deleted between the two queries.
            continue
        rows.append(
            WatchOutageOut(
                device_id=interval.device_id,
                device_name=name,
                started_at=interval.started_at,
                ended_at=interval.ended_at,
                duration_seconds=(
                    (interval.ended_at - interval.started_at).total_seconds()
                    if interval.ended_at
                    else None
                ),
                reason=interval.reason,
            )
        )
    return rows


@router.get("/devices/{device_id}", response_model=WatchDeviceOut)
async def get_device(
    device_id: str,
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.WATCH_READ))] = None,
) -> WatchDeviceOut:
    device = await db.get(WatchDevice, device_id)
    if device is None:
        raise NotFoundError.of("watch_device", device_id)
    observation = await db.scalar(
        select(WatchObservation).where(WatchObservation.device_id == device_id)
    )
    return _out(device, observation, await _probe_names(db), now=datetime.now(UTC))
