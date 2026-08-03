"""System status, dashboard and capabilities."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps.common import (
    AuditDep,
    DbSession,
    DockerDep,
    JobServiceDep,
    NatsDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import JobAccepted
from app.api.schemas.system import (
    AlertOut,
    AuditEventOut,
    CapabilitiesOut,
    CertificateOut,
    ContainerStateOut,
    DashboardOut,
    JetStreamOut,
    JobSummaryOut,
    NatsStateOut,
    SiteSettingsOut,
    SystemStatusOut,
)
from app.core.errors import ConflictError
from app.core.permissions import Permission
from app.infrastructure.certificates import CertificateInfo
from app.infrastructure.nats import NatsServerState
from app.services.jobs import JobRequest, ResourceRef
from app.services.system import SystemService, SystemStatus
from app.workers.handlers import system_actions

router = APIRouter(tags=["system"])


def get_system_service(
    db: DbSession,
    settings: SettingsDep,
    runtime: RuntimeDep,
    nats: NatsDep,
    docker: DockerDep,
) -> SystemService:
    return SystemService(db, settings, runtime, nats, docker)


SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]


def _certificate_out(info: CertificateInfo) -> CertificateOut:
    return CertificateOut(
        kind=info.kind,
        path=info.path,
        status=info.status,
        subject=info.subject,
        issuer=info.issuer,
        not_after=info.not_after,
        days_remaining=info.days_remaining,
        sha256=info.sha256,
        subject_alt_names=list(info.subject_alt_names),
        key_matches=info.key_matches,
    )


def _nats_out(state: NatsServerState) -> NatsStateOut:
    return NatsStateOut(
        available=state.available,
        healthy=state.healthy,
        server_name=state.server_name,
        version=state.version,
        uptime=state.uptime,
        connections=state.connections,
        slow_consumers=state.slow_consumers,
        jetstream=(
            None
            if state.jetstream is None
            else JetStreamOut(
                enabled=state.jetstream.enabled,
                streams=state.jetstream.streams,
                consumers=state.jetstream.consumers,
                messages=state.jetstream.messages,
                bytes_used=state.jetstream.bytes_used,
                store_used=state.jetstream.store_used,
                store_limit=state.jetstream.store_limit,
                store_usage_ratio=state.jetstream.store_usage_ratio,
            )
        ),
        connected_user_count=len(state.connected_users),
        error_details=state.error_details,
    )


def _status_out(status_data: SystemStatus, dev_auth: bool) -> SystemStatusOut:
    site = status_data.site
    return SystemStatusOut(
        site=SiteSettingsOut(
            nats_fqdn=site.nats_fqdn,
            nats_port=site.nats_port,
            nats_host_ip=site.nats_host_ip,
            ca_http_port=site.ca_http_port,
            ca_organization=site.ca_organization,
            prtg_core_ip=site.prtg_core_ip,
            nats_endpoint=site.nats_endpoint,
            is_configured=site.is_configured,
        ),
        nats=_nats_out(status_data.nats),
        containers=[
            ContainerStateOut(
                name=state.name,
                exists=state.exists,
                running=state.running,
                status=state.status,
                health=state.health,
                image=state.image,
                restart_count=state.restart_count,
            )
            for state in status_data.containers.values()
        ],
        certificates=[_certificate_out(c) for c in status_data.certificates],
        capabilities=CapabilitiesOut(
            docker=status_data.docker_available,
            runtime_state=status_data.runtime_state,
            dev_auth=dev_auth,
        ),
        runtime_missing=list(status_data.runtime_missing),
    )


@router.get("/system", response_model=SystemStatusOut)
async def system_status(
    service: SystemServiceDep,
    settings: SettingsDep,
    _: Annotated[object, Depends(require_permission(Permission.SYSTEM_READ))],
) -> SystemStatusOut:
    return _status_out(await service.status(), settings.dev_auth_enabled)


@router.get("/system/capabilities", response_model=CapabilitiesOut)
async def capabilities(
    docker: DockerDep, runtime: RuntimeDep, settings: SettingsDep, _: PrincipalDep
) -> CapabilitiesOut:
    """What this installation supports. Read by the interface on every load."""
    return CapabilitiesOut(
        docker=docker.available,
        runtime_state=runtime.health().state,
        dev_auth=settings.dev_auth_enabled,
    )


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    service: SystemServiceDep,
    probes: ProbeServiceDep,
    nats: NatsDep,
    settings: SettingsDep,
    _: Annotated[object, Depends(require_permission(Permission.SYSTEM_READ))],
) -> DashboardOut:
    connected = await nats.connected_users()
    summaries = await probes.list_summaries(
        connected, expected_ca_sha256=service.expected_ca_fingerprint()
    )
    data = await service.dashboard(summaries)
    return DashboardOut(
        system=_status_out(data.system, settings.dev_auth_enabled),
        probe_total=data.probe_total,
        probe_healthy=data.probe_healthy,
        probe_degraded=data.probe_degraded,
        probe_unreachable=data.probe_unreachable,
        probes_with_deviations=data.probes_with_deviations,
        failed_jobs_24h=data.failed_jobs_24h,
        running_jobs=data.running_jobs,
        expiring_certificates=[_certificate_out(c) for c in data.expiring_certificates],
        alerts=[AlertOut.model_validate(alert) for alert in data.alerts],
        recent_jobs=[JobSummaryOut.model_validate(job) for job in data.recent_jobs],
        recent_audit=[
            AuditEventOut.model_validate(event) for event in data.recent_audit
        ],
    )


@router.get("/certificates", response_model=list[CertificateOut])
async def certificates(
    service: SystemServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.CERTIFICATE_READ))],
) -> list[CertificateOut]:
    return [_certificate_out(info) for info in service.certificates()]


@router.post(
    "/certificates/server/renew",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def renew_server_certificate(
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        object, Depends(require_permission(Permission.CERTIFICATE_RENEW))
    ],
) -> JobAccepted:
    """Renew and activate the NATS server certificate.

    Restarts the container, so every probe reconnects. The interface asks for
    confirmation before it gets here.
    """
    job = await jobs.create(
        JobRequest(
            type=system_actions.RENEW_CERTIFICATE_JOB_TYPE,
            steps=system_actions.RENEW_CERTIFICATE_STEPS,
            resources=(
                ResourceRef("certificate", "server"),
                ResourceRef("nats", "server"),
            ),
            target_type="certificate",
            target_id="server",
            target_label="server certificate",
        ),
        principal,  # type: ignore[arg-type]
    )
    audit.record(
        action="certificate.renew",
        object_type="certificate",
        object_id="server",
        job_id=job.id,
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/system/setup", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def setup_stack(
    jobs: JobServiceDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SYSTEM_SETTINGS))
    ],
) -> JobAccepted:
    """Initialise the runtime: CA, server certificate, management key, shared
    account and server configuration - as a job with a live log.

    Only offered while runtime/ is incomplete; an existing installation is
    protected by the same refusal the retired shell setup had.
    """
    if runtime.health().state == "complete":
        raise ConflictError(
            params={"resource": "runtime"},
            details="the runtime is already initialised",
        )
    job = await jobs.create(
        JobRequest(
            type=system_actions.SETUP_JOB_TYPE,
            steps=system_actions.SETUP_STEPS,
            resources=(
                ResourceRef("nats", "server"),
                ResourceRef("certificate", "ca"),
            ),
            target_type="system",
            target_label="stack setup",
        ),
        principal,
    )
    audit.record(action="system.setup", object_type="system", job_id=job.id)
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/system/verify", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def verify_system(
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[object, Depends(require_permission(Permission.SYSTEM_READ))],
) -> JobAccepted:
    job = await jobs.create(
        JobRequest(
            type=system_actions.VERIFY_JOB_TYPE,
            steps=system_actions.VERIFY_STEPS,
            target_type="system",
            target_label="stack verification",
        ),
        principal,  # type: ignore[arg-type]
    )
    audit.record(action="system.verify", object_type="system", job_id=job.id)
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/system/backup", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def create_backup(
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        object, Depends(require_permission(Permission.SYSTEM_RESTART))
    ],
) -> JobAccepted:
    job = await jobs.create(
        JobRequest(
            type=system_actions.BACKUP_JOB_TYPE,
            steps=system_actions.BACKUP_STEPS,
            resources=(ResourceRef("nats", "server"),),
            target_type="system",
            target_label="JetStream backup",
        ),
        principal,  # type: ignore[arg-type]
    )
    audit.record(action="system.backup", object_type="system", job_id=job.id)
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/system/restart", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def restart_nats(
    jobs: JobServiceDep,
    docker: DockerDep,
    audit: AuditDep,
    principal: Annotated[
        object, Depends(require_permission(Permission.SYSTEM_RESTART))
    ],
) -> JobAccepted:
    """Restart the NATS container. Every probe and the core reconnect."""
    if not docker.available:
        from app.core.errors import DockerUnavailableError

        raise DockerUnavailableError()
    job = await jobs.create(
        JobRequest(
            type=system_actions.RESTART_JOB_TYPE,
            steps=system_actions.RESTART_STEPS,
            resources=(ResourceRef("nats", "server"),),
            target_type="system",
            target_label="prtg-nats",
        ),
        principal,  # type: ignore[arg-type]
    )
    audit.record(action="system.restart", object_type="system", job_id=job.id)
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )
