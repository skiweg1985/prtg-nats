"""The sensor catalogue and the parameter builder."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps.common import (
    CatalogDep,
    DbSession,
    require_permission,
)
from app.api.schemas.system import (
    RenderParametersIn,
    RenderParametersOut,
    SensorDetailOut,
    SensorFileOut,
    SensorSummaryOut,
)
from app.core.errors import ValidationFailedError
from app.core.permissions import Permission
from app.infrastructure.sensor_catalog import render_parameter_line
from app.persistence.models.inventory import ProbeObservedState, ProbeRecord

router = APIRouter(prefix="/sensors", tags=["sensors"])


async def _installation_counts(
    db: DbSession, catalogue_versions: dict[str, str]
) -> dict[str, tuple[int, int]]:
    """How many probes run each sensor, and how many are behind.

    Read from cached observed state so the catalogue page costs one query, not
    one SSH connection per probe.
    """
    counts: dict[str, tuple[int, int]] = {}
    rows = await db.scalars(select(ProbeObservedState))
    for row in rows:
        for entry in row.document.get("sensors", []):
            name = entry.get("name")
            if not name:
                continue
            installed, outdated = counts.get(name, (0, 0))
            expected = catalogue_versions.get(name)
            behind = bool(expected and entry.get("version") != expected)
            counts[name] = (installed + 1, outdated + (1 if behind else 0))
    return counts


@router.get("", response_model=list[SensorSummaryOut])
async def list_sensors(
    catalog: CatalogDep,
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> list[SensorSummaryOut]:
    definitions = catalog.list()
    versions = {definition.name: definition.version for definition in definitions}
    counts = await _installation_counts(db, versions)
    return [
        SensorSummaryOut(
            name=definition.name,
            version=definition.version,
            description=definition.description,
            needs_interface=definition.needs_interface,
            requires_privileged_helper=definition.requires_privileged_helper,
            iperf_kind=definition.iperf_kind,
            has_parameter_schema=definition.parameter_schema is not None,
            installed_on=counts.get(definition.name, (0, 0))[0],
            outdated_on=counts.get(definition.name, (0, 0))[1],
        )
        for definition in definitions
    ]


@router.get("/{name}", response_model=SensorDetailOut)
async def get_sensor(
    name: str,
    catalog: CatalogDep,
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> SensorDetailOut:
    definition = catalog.get(name)

    # Which probes report it installed.
    probes: list[str] = []
    rows = await db.execute(
        select(ProbeRecord.nats_username, ProbeObservedState.document).join(
            ProbeObservedState, ProbeObservedState.probe_id == ProbeRecord.id
        )
    )
    for username, document in rows:
        if any(entry.get("name") == name for entry in document.get("sensors", [])):
            probes.append(username)

    return SensorDetailOut(
        name=definition.name,
        version=definition.version,
        description=definition.description,
        needs_interface=definition.needs_interface,
        requires_privileged_helper=definition.requires_privileged_helper,
        iperf_kind=definition.iperf_kind,
        files=[
            SensorFileOut(
                slot=file.slot,
                relative_path=file.relative_path,
                size_bytes=file.size_bytes,
                sha256=file.sha256,
            )
            for file in definition.files
        ],
        parameter_schema=definition.parameter_schema,
        readme=definition.readme,
        profile_template=definition.profile_template,
        probes=sorted(probes),
    )


@router.get("/{name}/parameter-schema", response_model=dict[str, Any])
async def parameter_schema(
    name: str,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> dict[str, Any]:
    """The schema the interface turns into a form.

    Empty rather than 404 when a sensor ships none: the caller renders a plain
    text field in that case, which is still better than nothing.
    """
    definition = catalog.get(name)
    return definition.parameter_schema or {"fields": []}


@router.post("/{name}/render-parameters", response_model=RenderParametersOut)
async def render_parameters(
    name: str,
    payload: RenderParametersIn,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> RenderParametersOut:
    """Turn form values into the exact line to paste into PRTG."""
    definition = catalog.get(name)
    if definition.parameter_schema is None:
        raise ValidationFailedError(
            params={"sensor": name},
            details="this sensor does not ship a parameter schema",
        )
    return RenderParametersOut(
        parameters=render_parameter_line(definition.parameter_schema, payload.values)
    )
