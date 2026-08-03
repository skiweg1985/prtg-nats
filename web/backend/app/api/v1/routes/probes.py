"""Probe listing, detail, desired state and per-probe actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

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
from app.api.schemas.probes import (
    DesiredSensorIn,
    DesiredStateIn,
    DesiredStateOut,
    DeviationOut,
    ObservedStateOut,
    PlannedActionOut,
    ProbeDetailOut,
    ProbeInventoryOut,
    ProbeSummaryOut,
    ProbeUpdateIn,
    ReconciliationPlanOut,
    SensorStateOut,
)
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Permission
from app.domain.models import DesiredProbeState, DesiredSensor, ProbeSummary
from app.infrastructure.nats_runtime import NatsRuntime
from app.persistence.models.inventory import ProbeDesiredState
from app.services.jobs import JobRequest, ResourceRef
from app.services.probes import ProbeDetail
from app.services.system import SystemService
from app.workers.handlers import probe_actions, probe_lifecycle, sensor_actions

router = APIRouter(prefix="/probes", tags=["probes"])


def get_system_service(
    db: DbSession,
    settings: SettingsDep,
    runtime: RuntimeDep,
    nats: NatsDep,
    docker: DockerDep,
) -> SystemService:
    return SystemService(db, settings, runtime, nats, docker)


SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]


def _summary_out(summary: ProbeSummary) -> ProbeSummaryOut:
    return ProbeSummaryOut.model_validate(summary, from_attributes=True)


def _detail_out(detail: ProbeDetail) -> ProbeDetailOut:
    return ProbeDetailOut(
        summary=_summary_out(detail.summary),
        inventory=ProbeInventoryOut(
            ssh_host=detail.inventory.ssh_host,
            ssh_port=detail.inventory.ssh_port,
            probe_id=detail.inventory.probe_id,
            probe_name=detail.inventory.probe_name,
            access_key_present=detail.inventory.access_key_present,
            pending_transaction=detail.inventory.pending_transaction,
            assigned_sensors=list(detail.inventory.assigned_sensors),
            known_iperf_endpoints=list(detail.inventory.known_iperf_endpoints),
        ),
        observed=(
            None
            if detail.observed is None
            else ObservedStateOut(
                observed_at=detail.observed.observed_at,
                reachable=detail.observed.reachable,
                service=detail.observed.service,
                package_version=detail.observed.package_version,
                hostname=detail.observed.hostname,
                ca_sha256=detail.observed.ca_sha256,
                config_path=detail.observed.config_path,
                probe_id=detail.observed.probe_id,
                probe_name=detail.observed.probe_name,
                helper_version=detail.observed.helper_version,
                helper_sha256=detail.observed.helper_sha256,
                helper_outdated=detail.observed.helper_outdated,
                error_code=detail.observed.error_code,
                error_details=detail.observed.error_details,
            )
        ),
        sensors=[
            SensorStateOut(
                name=entry.name,
                status=entry.status,
                desired_version=entry.desired_version,
                installed_version=entry.installed_version,
                installed_sha256=entry.installed_sha256,
                expected_sha256=entry.expected_sha256,
                interfaces=list(entry.interfaces),
                helper_state=entry.helper_state,
            )
            for entry in detail.sensors
        ],
        deviations=[
            DeviationOut.model_validate(entry, from_attributes=True)
            for entry in detail.deviations
        ],
        notes=detail.record.notes,
        labels=detail.record.labels,
    )


@router.get("", response_model=list[ProbeSummaryOut])
async def list_probes(
    probes: ProbeServiceDep,
    system: SystemServiceDep,
    nats: NatsDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> list[ProbeSummaryOut]:
    """The probe table, from cached state.

    Deliberately does not contact any probe: an unreachable host must not make
    the list slow, and the freshness of each row is reported per row.
    """
    connected = await nats.connected_users()
    summaries = await probes.list_summaries(
        connected, expected_ca_sha256=system.expected_ca_fingerprint()
    )
    return [_summary_out(summary) for summary in summaries]


@router.get("/{probe_id}", response_model=ProbeDetailOut)
async def get_probe(
    probe_id: str,
    probes: ProbeServiceDep,
    system: SystemServiceDep,
    nats: NatsDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> ProbeDetailOut:
    connected = await nats.connected_users()
    detail = await probes.get_detail(
        probe_id,
        connected_users=connected,
        expected_ca_sha256=system.expected_ca_fingerprint(),
    )
    return _detail_out(detail)


@router.post("/{probe_id}/refresh", response_model=ObservedStateOut)
async def refresh_probe(
    probe_id: str,
    probes: ProbeServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> ObservedStateOut:
    """Ask this one probe for its state now.

    Synchronous rather than a job: it is one short round trip, the operator is
    looking at the page, and a job for a read would be ceremony.
    """
    record = await probes.get_record(probe_id)
    observed = await probes.refresh_observed_state(record.nats_username)
    return ObservedStateOut(
        observed_at=observed.observed_at,
        reachable=observed.reachable,
        service=observed.service,
        package_version=observed.package_version,
        hostname=observed.hostname,
        ca_sha256=observed.ca_sha256,
        config_path=observed.config_path,
        probe_id=observed.probe_id,
        probe_name=observed.probe_name,
        helper_version=observed.helper_version,
        helper_sha256=observed.helper_sha256,
        helper_outdated=observed.helper_outdated,
        error_code=observed.error_code,
        error_details=observed.error_details,
    )


@router.patch("/{probe_id}", response_model=ProbeDetailOut)
async def update_probe(
    probe_id: str,
    payload: ProbeUpdateIn,
    probes: ProbeServiceDep,
    system: SystemServiceDep,
    nats: NatsDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_UPDATE))],
) -> ProbeDetailOut:
    """Edit only what the web platform owns: label, notes, tags.

    Host, identity and credentials live in runtime/ and are changed by the
    workflows that own them, never by a form.
    """
    record = await probes.get_record(probe_id)
    before = {
        "display_name": record.display_name,
        "notes": record.notes,
        "labels": dict(record.labels),
    }
    if payload.display_name is not None:
        record.display_name = payload.display_name
    if payload.notes is not None:
        record.notes = payload.notes
    if payload.labels is not None:
        record.labels = payload.labels

    audit.record(
        action="probe.update",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        before=before,
        after={
            "display_name": record.display_name,
            "notes": record.notes,
            "labels": dict(record.labels),
        },
    )
    connected = await nats.connected_users()
    detail = await probes.get_detail(
        probe_id,
        connected_users=connected,
        expected_ca_sha256=system.expected_ca_fingerprint(),
    )
    return _detail_out(detail)


# --- Desired state ----------------------------------------------------------


@router.get("/{probe_id}/desired-state", response_model=DesiredStateOut)
async def get_desired_state(
    probe_id: str,
    probes: ProbeServiceDep,
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> DesiredStateOut:
    record = await probes.get_record(probe_id)
    row = await db.scalar(
        select(ProbeDesiredState).where(
            ProbeDesiredState.probe_id == record.id,
            ProbeDesiredState.is_current.is_(True),
        )
    )
    if row is None:
        # No explicit intent yet; report what the shell tooling recorded so the
        # form opens on something true rather than empty.
        inventory = probes.inventory_for(record.nats_username)
        return DesiredStateOut(
            version=0,
            sensors=[DesiredSensorIn(name=name) for name in inventory.assigned_sensors],
            probe_name=inventory.probe_name,
            ca_required=True,
        )
    desired = DesiredProbeState.from_document(row.document)
    return DesiredStateOut(
        version=row.version,
        sensors=[
            DesiredSensorIn(
                name=sensor.name,
                version=sensor.version,
                profiles=list(sensor.profiles),
                interfaces=list(sensor.interfaces),
            )
            for sensor in desired.sensors
        ],
        probe_name=desired.probe_name,
        ca_required=desired.ca_required,
        updated_at=row.updated_at,
        author_name=row.author_name,
    )


@router.put("/{probe_id}/desired-state", response_model=DesiredStateOut)
async def set_desired_state(
    probe_id: str,
    payload: DesiredStateIn,
    probes: ProbeServiceDep,
    db: DbSession,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> DesiredStateOut:
    """Record what should be true. Applying it is a separate, explicit step."""
    record = await probes.get_record(probe_id)
    current = await db.scalar(
        select(ProbeDesiredState).where(
            ProbeDesiredState.probe_id == record.id,
            ProbeDesiredState.is_current.is_(True),
        )
    )
    document = DesiredProbeState(
        sensors=tuple(
            DesiredSensor(
                name=sensor.name,
                version=sensor.version,
                profiles=tuple(sensor.profiles),
                interfaces=tuple(sensor.interfaces),
            )
            for sensor in payload.sensors
        ),
        probe_name=payload.probe_name,
        ca_required=payload.ca_required,
    ).to_document()

    version = (current.version + 1) if current is not None else 1
    if current is not None:
        current.is_current = False
    row = ProbeDesiredState(
        probe_id=record.id,
        version=version,
        is_current=True,
        document=document,
        author_id=principal.user_id,
        author_name=principal.username,
    )
    db.add(row)
    await db.flush()

    audit.record(
        action="probe.set_desired_state",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        before=None if current is None else current.document,
        after=document,
    )
    return DesiredStateOut(
        version=version,
        sensors=payload.sensors,
        probe_name=payload.probe_name,
        ca_required=payload.ca_required,
        updated_at=row.updated_at,
        author_name=principal.username,
    )


@router.get("/{probe_id}/deviations", response_model=list[DeviationOut])
async def get_deviations(
    probe_id: str,
    probes: ProbeServiceDep,
    system: SystemServiceDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> list[DeviationOut]:
    detail = await probes.get_detail(
        probe_id,
        connected_users=frozenset(),
        expected_ca_sha256=system.expected_ca_fingerprint(),
    )
    return [
        DeviationOut.model_validate(entry, from_attributes=True)
        for entry in detail.deviations
    ]


@router.post("/{probe_id}/reconcile", response_model=None)
async def reconcile(
    probe_id: str,
    probes: ProbeServiceDep,
    system: SystemServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_RECONCILE))
    ],
    dry_run: Annotated[bool, Query()] = True,
) -> ReconciliationPlanOut | JSONResponse:
    """Preview with ``dry_run=true`` (the default), execute without it.

    The preview costs nothing and changes nothing. The execution is a job that
    replans from fresh observation first - the probe may have changed since
    the operator looked at the preview.
    """
    if dry_run:
        plan = await probes.plan_reconciliation(
            probe_id, expected_ca_sha256=system.expected_ca_fingerprint()
        )
        return ReconciliationPlanOut(
            probe_username=plan.probe_username,
            deviations=[
                DeviationOut.model_validate(entry, from_attributes=True)
                for entry in plan.deviations
            ],
            actions=[
                PlannedActionOut.model_validate(action, from_attributes=True)
                for action in plan.actions
            ],
            restarts_service=plan.restarts_service,
            is_empty=plan.is_empty,
        )

    record = await probes.get_record(probe_id)
    desired = await probes.desired_document(record)
    job = await jobs.create(
        JobRequest(
            type=probe_lifecycle.RECONCILE_JOB_TYPE,
            steps=probe_lifecycle.RECONCILE_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={"probe": record.nats_username, "desired": desired},
        ),
        principal,
    )
    audit.record(
        action="probe.reconcile",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        job_id=job.id,
    )
    accepted = JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )
    return JSONResponse(status_code=202, content=accepted.model_dump())


@router.post(
    "/{probe_id}/configure",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def configure_probe(
    probe_id: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    """Render and roll out the probe configuration transactionally.

    What "probe configure" did in the shell, as a job: identity resolved or
    created, template rendered with the account password, staged, activated
    with the probe-side check, committed - rolled back if any of it fails.
    """
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=probe_lifecycle.CONFIGURE_JOB_TYPE,
            steps=probe_lifecycle.CONFIGURE_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={"probe": record.nats_username},
        ),
        principal,
    )
    audit.record(
        action="probe.configure",
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


@router.delete(
    "/{probe_id}", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def unenroll_probe(
    probe_id: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_DELETE))
    ],
    remove_sensors: Annotated[
        bool,
        Query(description="Remove every sensor from the probe before retiring it"),
    ] = False,
    uninstall_mpp: Annotated[
        bool,
        Query(description="Uninstall the probe software, its config and the CA"),
    ] = False,
    delete_account: Annotated[
        bool, Query(description="Delete the NATS account once the inventory is gone")
    ] = False,
) -> JobAccepted:
    """Retire a probe, optionally clearing everything this platform put on it.

    Bare, this removes the management access and the inventory; the probe
    keeps running and stays connected. Each option is a separate decision
    with its own permission, because each destroys something the plain
    unenroll deliberately leaves alone.

    The lock means this waits for any job still working on the probe instead
    of pulling the access out from under it.
    """
    record = await probes.get_record(probe_id)
    if remove_sensors and not principal.has(Permission.SENSOR_REMOVE):
        raise PermissionDeniedError.of(Permission.SENSOR_REMOVE.value)
    if uninstall_mpp and not principal.has(Permission.PROBE_UPDATE):
        raise PermissionDeniedError.of(Permission.PROBE_UPDATE.value)
    if delete_account:
        if not principal.has(Permission.CREDENTIAL_ROTATE):
            raise PermissionDeniedError.of(Permission.CREDENTIAL_ROTATE.value)
        # Checked here rather than in the job: the account is deleted last,
        # so a refusal there would arrive after the probe has already lost
        # its access. The job repeats the check - this only keeps the common
        # case from becoming a half-finished retirement.
        if NatsRuntime(settings).is_last_account(record.nats_username):
            raise ConflictError(
                params={"resource": "nats_account"},
                details="refusing to remove the last NATS account",
            )

    job = await jobs.create(
        JobRequest(
            type=probe_lifecycle.UNENROLL_JOB_TYPE,
            steps=probe_lifecycle.unenroll_steps(
                remove_sensors=remove_sensors,
                uninstall_mpp=uninstall_mpp,
                delete_account=delete_account,
            ),
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={
                "probe": record.nats_username,
                "remove_sensors": remove_sensors,
                "uninstall_mpp": uninstall_mpp,
                "delete_account": delete_account,
            },
        ),
        principal,
    )
    audit.record(
        action="probe.delete",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        job_id=job.id,
        after={
            "remove_sensors": remove_sensors,
            "uninstall_mpp": uninstall_mpp,
            "delete_account": delete_account,
        },
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/{probe_id}/sensors/{sensor_name}/remove",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def remove_sensor_from_probe(
    probe_id: str,
    sensor_name: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_REMOVE))
    ],
) -> JobAccepted:
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=sensor_actions.REMOVE_JOB_TYPE,
            steps=sensor_actions.REMOVE_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=f"{sensor_name} @ {record.nats_username}",
            payload={"probe": record.nats_username, "sensor": sensor_name},
        ),
        principal,
    )
    audit.record(
        action="sensor.remove",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        after={"sensor": sensor_name},
        job_id=job.id,
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


# --- Actions ----------------------------------------------------------------


@router.post(
    "/{probe_id}/install-ca",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_ca(
    probe_id: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=probe_actions.INSTALL_CA_JOB_TYPE,
            steps=probe_actions.INSTALL_CA_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={"probe": record.nats_username},
        ),
        principal,
    )
    audit.record(
        action="probe.install_ca",
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


@router.post(
    "/{probe_id}/helper-update",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_probe_helper(
    probe_id: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    """Renew the management helper on the probe.

    Only reaches probes whose helper already knows the request. One that does
    not has to be enrolled again - the file is signed, and the key that proves
    it travels over the bootstrap path alone.
    """
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=probe_actions.HELPER_UPDATE_JOB_TYPE,
            steps=probe_actions.HELPER_UPDATE_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={"probe": record.nats_username},
        ),
        principal,
    )
    audit.record(
        action="probe.helper_update",
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


@router.post(
    "/{probe_id}/validate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_probe(
    probe_id: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_READ))
    ],
) -> JobAccepted:
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=probe_actions.VALIDATE_JOB_TYPE,
            steps=probe_actions.VALIDATE_STEPS,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=record.nats_username,
            payload={"probe": record.nats_username},
        ),
        principal,
    )
    audit.record(
        action="probe.validate",
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


@router.get("/{probe_id}/access-key", response_model=dict[str, str])
async def reveal_access_key(
    probe_id: str,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_READ))],
) -> dict[str, str]:
    """Show the PRTG access key.

    A deliberate, audited disclosure: an operator needs the value to paste into
    PRTG, and every reveal leaves a record of who looked.
    """
    record = await probes.get_record(probe_id)
    access_key = runtime.read_access_key(record.nats_username)
    if access_key is None:
        raise NotFoundError.of("access_key", record.nats_username)
    audit.record(
        action="credential.reveal",
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        comment="PRTG access key revealed",
    )
    return {"access_key": access_key}
