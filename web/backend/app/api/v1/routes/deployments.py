"""Sensor rollouts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    CatalogDep,
    DbSession,
    JobServiceDep,
    PrincipalDep,
    ProbeServiceDep,
    require_permission,
)
from app.api.schemas.system import (
    DeploymentCreateIn,
    DeploymentOut,
    DeploymentTargetOut,
)
from app.core.errors import NotFoundError
from app.core.permissions import Permission
from app.domain.enums import JobStatus
from app.persistence.models.inventory import Deployment, DeploymentTarget
from app.services.jobs import JobRequest, ResourceRef
from app.workers.handlers import deploy_sensor

router = APIRouter(prefix="/deployments", tags=["deployments"])


def _out(deployment: Deployment) -> DeploymentOut:
    return DeploymentOut(
        id=deployment.id,
        sensor_name=deployment.sensor_name,
        sensor_version=deployment.sensor_version,
        status=deployment.status,
        job_id=deployment.job_id,
        dry_run=deployment.dry_run,
        requested_by_name=deployment.requested_by_name,
        created_at=deployment.created_at,
        targets=[
            DeploymentTargetOut.model_validate(target) for target in deployment.targets
        ],
    )


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.DEPLOYMENT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DeploymentOut]:
    records = await db.scalars(
        select(Deployment).order_by(Deployment.id.desc()).limit(limit)
    )
    return [_out(record) for record in records]


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(
    deployment_id: str,
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.DEPLOYMENT_READ))],
) -> DeploymentOut:
    record = await db.get(Deployment, deployment_id)
    if record is None:
        raise NotFoundError.of("deployment", deployment_id)
    return _out(record)


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    payload: DeploymentCreateIn,
    db: DbSession,
    catalog: CatalogDep,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.DEPLOYMENT_CREATE))
    ],
) -> DeploymentOut:
    """Roll one sensor out to one or more probes.

    One job, one lock per probe. If another job already holds a probe, the
    rollout waits for it rather than racing it - two deployments writing the
    same sensor directory is exactly the situation the lock exists for.
    """
    definition = catalog.get(payload.sensor)

    records = []
    for probe_id in payload.probe_ids:
        record = await probes.get_record(probe_id)
        records.append(record)

    deployment = Deployment(
        sensor_name=definition.name,
        sensor_version=definition.version,
        status=JobStatus.QUEUED,
        dry_run=payload.dry_run,
        requested_by_name=principal.username,
    )
    db.add(deployment)
    await db.flush()

    for record in records:
        db.add(
            DeploymentTarget(
                deployment_id=deployment.id,
                probe_id=record.id,
                probe_label=record.nats_username,
            )
        )

    job = await jobs.create(
        JobRequest(
            type=deploy_sensor.JOB_TYPE,
            steps=deploy_sensor.STEPS,
            resources=tuple(ResourceRef("probe", record.id) for record in records),
            target_type="deployment",
            target_id=deployment.id,
            target_label=f"{definition.name} → {len(records)} probe(s)",
            payload={
                "sensor": definition.name,
                "probes": [record.nats_username for record in records],
                "dry_run": payload.dry_run,
                "deployment_id": deployment.id,
            },
        ),
        principal,
    )
    deployment.job_id = job.id
    await db.flush()
    await db.refresh(deployment, ["targets"])

    audit.record(
        action="sensor.deploy",
        object_type="deployment",
        object_id=deployment.id,
        object_label=definition.name,
        after={
            "sensor": definition.name,
            "version": definition.version,
            "probes": [record.nats_username for record in records],
            "dry_run": payload.dry_run,
        },
        job_id=job.id,
    )
    return _out(deployment)
