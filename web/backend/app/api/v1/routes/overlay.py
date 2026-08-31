"""The overlay: which probes are on the tunnel, and what each one does with it.

Reading is synchronous - the hub state is files in the runtime and one look at
the interface. Everything that touches a probe is a job, for the reason
everything that touches a probe is: it can hang on a host that stopped
answering, and it belongs in the audit trail.

Turning it on and off is here as well, behind its own administrator-only
permission: it creates a container with network-admin rights in the host's
network namespace, which is the same decision ``system.update`` guards.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field

from app.api.deps.common import (
    AuditDep,
    DockerDep,
    HelperDep,
    JobServiceDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel, JobAccepted
from app.core.permissions import Permission
from app.infrastructure.overlay import OverlayRuntime, OverlayStatus
from app.infrastructure.runtime_files import RuntimeFileStore
from app.services.jobs import JobRequest, ResourceRef
from app.services.overlay import OverlayService
from app.workers.handlers import overlay as overlay_jobs

router = APIRouter(prefix="/overlay", tags=["infrastructure"])

OverlayMode = Literal["off", "auto", "on"]


class OverlayPeerOut(ApiModel):
    nats_username: str
    address: str
    public_key: str
    mode: str
    # What the probe last reported it was doing, which is not the same as the
    # mode: "auto" says when the tunnel may be used, this says whether it is.
    last_state: str | None = None


class OverlayOut(ApiModel):
    enabled: bool
    endpoint: str | None
    # Split out as well, because the form that sets them edits these two and
    # not the joined string.
    endpoint_host: str | None
    port: int
    subnet: str
    hub_address: str
    hub_public_key: str | None
    default_mode: str
    interface_up: bool
    peers: list[OverlayPeerOut]


class OverlayEnableIn(ApiModel):
    """What an administrator has to decide to turn the overlay on."""

    # The address probes dial. Not NATS_FQDN by default: it has to be
    # reachable exactly when NATS_FQDN is not.
    endpoint_host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    subnet: str | None = Field(default=None, max_length=64)
    default_mode: OverlayMode | None = None


class OverlayActionIn(ApiModel):
    probe_ids: list[str] = Field(min_length=1)


class OverlayAttachIn(OverlayActionIn):
    mode: OverlayMode | None = None


class OverlayModeIn(OverlayActionIn):
    mode: OverlayMode
    # Only for taking a probe off the tunnel it is reached through. Without it
    # the job refuses when the ordinary address does not answer, because the
    # request would cut the session carrying it.
    force: bool = False


class OverlayDetachIn(OverlayActionIn):
    force: bool = False


@router.get("", response_model=OverlayOut)
async def read_overlay(
    settings: SettingsDep,
    runtime: RuntimeDep,
    _: Annotated[PrincipalDep, Depends(require_permission(Permission.OVERLAY_READ))],
) -> OverlayOut:
    return _overlay_out(OverlayRuntime(settings).status(), runtime)


def _overlay_out(state: OverlayStatus, runtime: RuntimeFileStore) -> OverlayOut:
    modes = {
        probe.nats_username: probe.overlay_last_state
        for probe in runtime.read_all_probes()
    }
    return OverlayOut(
        enabled=state.enabled,
        endpoint=state.endpoint,
        endpoint_host=state.settings.endpoint_host,
        port=state.settings.port,
        subnet=state.subnet,
        hub_address=state.hub_address,
        hub_public_key=state.hub_public_key,
        default_mode=state.default_mode,
        interface_up=state.interface_up,
        peers=[
            OverlayPeerOut(
                nats_username=peer.nats_username,
                address=peer.address,
                public_key=peer.public_key,
                mode=peer.mode,
                last_state=modes.get(peer.nats_username),
            )
            for peer in state.peers
        ],
    )


@router.post("/enable", response_model=OverlayOut)
async def enable_overlay(
    payload: OverlayEnableIn,
    settings: SettingsDep,
    runtime: RuntimeDep,
    helper: HelperDep,
    docker: DockerDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_ENABLE))
    ],
) -> OverlayOut:
    """Turn the overlay on and start the hub.

    Synchronous rather than a job: it is a settings form, and the mistakes it
    can make - an endpoint that is the NATS address, a subnet that probes
    already hold addresses from - are worth refusing in front of the person
    typing rather than in a job log.
    """
    service = OverlayService(settings, helper, docker)
    state = await service.enable(
        endpoint_host=payload.endpoint_host,
        port=payload.port,
        subnet=payload.subnet,
        default_mode=payload.default_mode,
    )
    audit.record(
        action="overlay.enable",
        object_type="overlay",
        object_id="overlay",
        object_label=payload.endpoint_host,
    )
    return _overlay_out(state, runtime)


@router.post("/disable", response_model=OverlayOut)
async def disable_overlay(
    settings: SettingsDep,
    runtime: RuntimeDep,
    helper: HelperDep,
    docker: DockerDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_ENABLE))
    ],
) -> OverlayOut:
    """Stop the hub. Every peer keeps its address and its key."""
    service = OverlayService(settings, helper, docker)
    state = await service.disable()
    audit.record(
        action="overlay.disable",
        object_type="overlay",
        object_id="overlay",
        object_label="overlay",
    )
    return _overlay_out(state, runtime)


async def _overlay_job(
    payload: OverlayActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: PrincipalDep,
    *,
    job_type: str,
    steps: tuple[str, ...],
    action: str,
    extra: dict[str, object] | None = None,
) -> JobAccepted:
    records = [await probes.get_record(probe_id) for probe_id in payload.probe_ids]
    single = records[0] if len(records) == 1 else None
    job = await jobs.create(
        JobRequest(
            type=job_type,
            steps=steps,
            resources=tuple(ResourceRef("probe", record.id) for record in records),
            target_type="probe" if single else None,
            target_id=single.id if single else None,
            target_label=(
                single.nats_username if single else f"{len(records)} probe(s)"
            ),
            payload={
                "probes": [record.nats_username for record in records],
                **(extra or {}),
            },
        ),
        principal,
    )
    for record in records:
        audit.record(
            action=action,
            object_type="probe",
            object_id=record.id,
            object_label=record.nats_username,
            job_id=job.id,
        )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post("/peers", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def attach_probes(
    payload: OverlayAttachIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_MANAGE))
    ],
) -> JobAccepted:
    """Give the selected probes an address and configure their tunnel."""
    return await _overlay_job(
        payload,
        probes,
        jobs,
        audit,
        principal,
        job_type=overlay_jobs.ATTACH_JOB_TYPE,
        steps=overlay_jobs.ATTACH_STEPS,
        action="overlay.attach",
        extra={"mode": payload.mode} if payload.mode else None,
    )


@router.post(
    "/peers/mode", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def change_mode(
    payload: OverlayModeIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_MANAGE))
    ],
) -> JobAccepted:
    """Change when the selected probes' NATS traffic takes the tunnel."""
    return await _overlay_job(
        payload,
        probes,
        jobs,
        audit,
        principal,
        job_type=overlay_jobs.MODE_JOB_TYPE,
        steps=overlay_jobs.MODE_STEPS,
        action="overlay.mode",
        extra={"mode": payload.mode, "force": payload.force},
    )


@router.post(
    "/peers/remove", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def detach_probes(
    payload: OverlayDetachIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_MANAGE))
    ],
) -> JobAccepted:
    """Take the selected probes off the overlay and free their addresses."""
    return await _overlay_job(
        payload,
        probes,
        jobs,
        audit,
        principal,
        job_type=overlay_jobs.DETACH_JOB_TYPE,
        steps=overlay_jobs.DETACH_STEPS,
        action="overlay.detach",
        extra={"force": payload.force},
    )


@router.post(
    "/peers/refresh", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def refresh_peers(
    payload: OverlayActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.OVERLAY_READ))
    ],
) -> JobAccepted:
    """Ask the selected probes which path they are on right now."""
    return await _overlay_job(
        payload,
        probes,
        jobs,
        audit,
        principal,
        job_type=overlay_jobs.REFRESH_JOB_TYPE,
        steps=overlay_jobs.REFRESH_STEPS,
        action="overlay.refresh",
    )
