"""Probe listing, detail, desired state and per-probe actions."""

from __future__ import annotations

from datetime import UTC, datetime
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
    AccessKeyOut,
    DesiredSensorIn,
    DesiredStateIn,
    DesiredStateOut,
    DeviationOut,
    ObservedStateOut,
    PlannedActionOut,
    ProbeActionIn,
    ProbeDetailOut,
    ProbeInventoryOut,
    ProbeSummaryOut,
    ProbeUpdateIn,
    ReconciliationPlanOut,
    SensorStateOut,
)
from app.api.schemas.system import WirelessInterfaceOut
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Permission
from app.domain.models import DesiredProbeState, DesiredSensor, ProbeSummary
from app.infrastructure.nats_runtime import NatsRuntime
from app.persistence.models.inventory import ProbeDesiredState, ProbeRecord
from app.services.audit import AuditWriter
from app.services.auth import Principal
from app.services.jobs import JobRequest, JobService, ResourceRef
from app.services.probes import ProbeDetail, ProbeService
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
        prtg_registered_at=detail.record.prtg_registered_at,
        prtg_registered_by=detail.record.prtg_registered_by,
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


# --- Actions on a selection -------------------------------------------------
#
# Every route here is registered before the "/{probe_id}/..." ones below, and
# that is load-bearing rather than tidy: FastAPI serves the first route whose
# path matches, so with the single-probe routes first, "/probes/actions/refresh"
# would be read as a refresh of the probe named "actions".
#
# The job they create is the one a rollout already uses: a single job holding
# one lock per probe, so a selection of twelve queues behind whatever else is
# working on those twelve instead of racing it.


async def _selected_records(
    probes: ProbeService, payload: ProbeActionIn
) -> list[ProbeRecord]:
    """Resolve every id before any job exists.

    One unknown id fails the request with 404 and nothing has run. Resolving
    them inside the job would leave a half-applied action and a job to read.
    """
    return [await probes.get_record(probe_id) for probe_id in payload.probe_ids]


async def _fleet_job(
    records: list[ProbeRecord],
    jobs: JobService,
    audit: AuditWriter,
    principal: Principal,
    *,
    job_type: str,
    steps: tuple[str, ...],
    action: str,
    extra_payload: dict[str, object] | None = None,
) -> JobAccepted:
    """One action, one job, however many probes were selected."""
    single = records[0] if len(records) == 1 else None
    job = await jobs.create(
        JobRequest(
            type=job_type,
            steps=steps,
            resources=tuple(ResourceRef("probe", record.id) for record in records),
            # A selection has no one target: naming twelve probes in the label
            # would fill the job list with a paragraph, and "jobs for this
            # probe" would then answer with a job that is mostly about others.
            # The per-probe outcome is in the job log, where it belongs.
            target_type="probe" if single else None,
            target_id=single.id if single else None,
            target_label=(
                single.nats_username if single else f"{len(records)} probe(s)"
            ),
            payload={
                "probes": [record.nats_username for record in records],
                **(extra_payload or {}),
            },
        ),
        principal,
    )
    # One entry per probe, not one per request: "who installed the CA on
    # berlin-01" has to be answerable whether it was pressed on a detail page
    # or came out of a selection of twelve.
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


@router.post(
    "/actions/refresh",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_READ))
    ],
) -> JobAccepted:
    """Ask a selection of probes for their state.

    A job where the single-probe route is a synchronous call: one round trip
    is worth waiting for, a dozen over SSH is not.
    """
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_actions.REFRESH_JOB_TYPE,
        steps=probe_actions.REFRESH_STEPS,
        action="probe.refresh",
    )


@router.post(
    "/actions/validate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_READ))
    ],
) -> JobAccepted:
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_actions.VALIDATE_JOB_TYPE,
        steps=probe_actions.VALIDATE_STEPS,
        action="probe.validate",
    )


@router.post(
    "/actions/install-ca",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_ca_on_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_actions.INSTALL_CA_JOB_TYPE,
        steps=probe_actions.INSTALL_CA_STEPS,
        action="probe.install_ca",
    )


@router.post(
    "/actions/helper-update",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_helper_on_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    """Renew the helper on a selection.

    The probe list already knows which rows need it - helper_outdated is a
    column - and this is what turns that column into one action instead of one
    visit per row.
    """
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_actions.HELPER_UPDATE_JOB_TYPE,
        steps=probe_actions.HELPER_UPDATE_STEPS,
        action="probe.helper_update",
    )


@router.post(
    "/actions/configure",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def configure_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
) -> JobAccepted:
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_lifecycle.CONFIGURE_JOB_TYPE,
        steps=probe_lifecycle.CONFIGURE_STEPS,
        action="probe.configure",
    )


@router.post(
    "/actions/reconcile",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reconcile_selected_probes(
    payload: ProbeActionIn,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_RECONCILE))
    ],
) -> JobAccepted:
    """Execute the plan on a selection - never a preview.

    The dry run stays on the single-probe route: a preview is something an
    operator reads, and twelve of them at once is not a thing to read.
    """
    records = await _selected_records(probes, payload)
    return await _fleet_job(
        records,
        jobs,
        audit,
        principal,
        job_type=probe_lifecycle.RECONCILE_JOB_TYPE,
        steps=probe_lifecycle.RECONCILE_STEPS,
        action="probe.reconcile",
        # Each probe plans against its own desired state; there is no such
        # thing as one desired state for a selection.
        extra_payload={
            "desired_by_probe": {
                record.nats_username: await probes.desired_document(record)
                for record in records
            }
        },
    )


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
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_UPDATE))
    ],
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
        "prtg_registered": record.prtg_registered_at is not None,
    }
    if payload.display_name is not None:
        record.display_name = payload.display_name
    if payload.notes is not None:
        record.notes = payload.notes
    if payload.labels is not None:
        record.labels = payload.labels
    # The one manual state the platform cannot observe: the access key entered
    # in PRTG and the probe approved there. Who ticked it and when is the
    # whole value of the record.
    if payload.prtg_registered is True:
        record.prtg_registered_at = datetime.now(UTC)
        record.prtg_registered_by = principal.username
    elif payload.prtg_registered is False:
        record.prtg_registered_at = None
        record.prtg_registered_by = None

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
            "prtg_registered": record.prtg_registered_at is not None,
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


@router.get(
    "/{probe_id}/wireless-interfaces",
    response_model=list[WirelessInterfaceOut],
)
async def list_wireless_interfaces(
    probe_id: str,
    probes: ProbeServiceDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_READ))
    ],
) -> list[WirelessInterfaceOut]:
    """The radio interfaces of one probe, asked live.

    Reserving one takes it away from whatever it is doing, so the choice is
    made against the current state and not against a cached one.
    """
    record = await probes.get_record(probe_id)
    interfaces = await probes.wireless_interfaces(record.nats_username)
    return [WirelessInterfaceOut.model_validate(entry) for entry in interfaces]


@router.post(
    "/{probe_id}/sensors/{sensor_name}/interfaces/{interface}/reserve",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reserve_probe_interface(
    probe_id: str,
    sensor_name: str,
    interface: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> JobAccepted:
    return await _interface_job(
        probes,
        jobs,
        audit,
        principal,
        probe_id=probe_id,
        sensor_name=sensor_name,
        interface=interface,
        job_type=sensor_actions.RESERVE_INTERFACE_JOB_TYPE,
        steps=sensor_actions.RESERVE_INTERFACE_STEPS,
        action="sensor.reserve_interface",
    )


@router.post(
    "/{probe_id}/sensors/{sensor_name}/interfaces/{interface}/release",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def release_probe_interface(
    probe_id: str,
    sensor_name: str,
    interface: str,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> JobAccepted:
    return await _interface_job(
        probes,
        jobs,
        audit,
        principal,
        probe_id=probe_id,
        sensor_name=sensor_name,
        interface=interface,
        job_type=sensor_actions.RELEASE_INTERFACE_JOB_TYPE,
        steps=sensor_actions.RELEASE_INTERFACE_STEPS,
        action="sensor.release_interface",
    )


async def _interface_job(
    probes: ProbeService,
    jobs: JobService,
    audit: AuditWriter,
    principal: Principal,
    *,
    probe_id: str,
    sensor_name: str,
    interface: str,
    job_type: str,
    steps: tuple[str, ...],
    action: str,
) -> JobAccepted:
    """Reserving and releasing differ in one helper call and nothing else."""
    record = await probes.get_record(probe_id)
    job = await jobs.create(
        JobRequest(
            type=job_type,
            steps=steps,
            resources=(ResourceRef("probe", record.id),),
            target_type="probe",
            target_id=record.id,
            target_label=f"{interface} @ {record.nats_username}",
            payload={
                "probe": record.nats_username,
                "sensor": sensor_name,
                "interface": interface,
            },
        ),
        principal,
    )
    audit.record(
        action=action,
        object_type="probe",
        object_id=record.id,
        object_label=record.nats_username,
        after={"sensor": sensor_name, "interface": interface},
        job_id=job.id,
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


@router.get("/{probe_id}/access-key", response_model=AccessKeyOut)
async def reveal_access_key(
    probe_id: str,
    probes: ProbeServiceDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_READ))],
) -> AccessKeyOut:
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
    return AccessKeyOut(nats_username=record.nats_username, access_key=access_key)
