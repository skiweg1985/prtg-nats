"""The sensor catalogue, the parameter reference and the parameter builder."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    CatalogDep,
    DbSession,
    JobServiceDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    require_permission,
)
from app.api.schemas.common import JobAccepted
from app.api.schemas.system import (
    FileFieldOut,
    ParameterFieldOut,
    ProfileFieldOut,
    RenderParametersIn,
    RenderParametersOut,
    SensorDetailOut,
    SensorFileOut,
    SensorInstallationOut,
    SensorProfileDetailOut,
    SensorProfileFileIn,
    SensorProfileFileOut,
    SensorProfileIn,
    SensorProfileOut,
    SensorSchemaOut,
    SensorSummaryOut,
)
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.permissions import Permission
from app.infrastructure.probe_helper.protocol import probe_profile_file_path
from app.infrastructure.runtime_files import (
    NAME_PATTERN,
    RuntimeFileStore,
    SensorProfileRecord,
)
from app.infrastructure.sensor_catalog import (
    SensorDefinition,
    SensorSchema,
    default_parameter_line,
    profile_parameter_line,
    render_parameter_line,
)
from app.persistence.models.inventory import ProbeObservedState, ProbeRecord
from app.services.jobs import JobRequest
from app.workers.handlers import sensor_actions

router = APIRouter(prefix="/sensors", tags=["sensors"])


def _schema_out(schema: SensorSchema) -> SensorSchemaOut:
    """The declaration as the interface consumes it.

    The field models mirror the catalogue's dataclasses one for one, so they
    are validated from attributes rather than copied field by field - a new
    attribute then reaches the interface by being declared in both places, not
    by remembering to add a line here.
    """
    return SensorSchemaOut(
        parameters=[
            ParameterFieldOut.model_validate(entry, from_attributes=True)
            for entry in schema.parameters
        ],
        settings=[
            ProfileFieldOut.model_validate(entry, from_attributes=True)
            for entry in schema.settings
        ],
        credentials=[
            ProfileFieldOut.model_validate(entry, from_attributes=True)
            for entry in schema.credentials
        ],
        files=[
            FileFieldOut.model_validate(entry, from_attributes=True)
            for entry in schema.files
        ],
        supports_profiles=schema.supports_profiles,
        default_parameter_line=default_parameter_line(schema),
    )


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
            has_parameter_schema=definition.schema is not None,
            supports_profiles=definition.supports_profiles,
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

    # Which probes report it installed, and at which version - the answer
    # "outdated on twelve" is only useful together with which twelve.
    installations: list[SensorInstallationOut] = []
    rows = await db.execute(
        select(ProbeRecord.nats_username, ProbeObservedState.document).join(
            ProbeObservedState, ProbeObservedState.probe_id == ProbeRecord.id
        )
    )
    for username, document in rows:
        for entry in document.get("sensors", []):
            if entry.get("name") != name:
                continue
            version = str(entry.get("version") or "")
            installations.append(
                SensorInstallationOut(
                    probe=username,
                    version=version,
                    current=version == definition.version,
                )
            )

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
        parameter_schema=None
        if definition.schema is None
        else _schema_out(definition.schema),
        readme=definition.readme,
        profile_template=definition.profile_template,
        installations=sorted(installations, key=lambda entry: entry.probe),
    )


@router.get("/{name}/parameter-schema", response_model=SensorSchemaOut)
async def parameter_schema(
    name: str,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> SensorSchemaOut:
    """What the sensor declares, for the reference and the forms.

    Empty rather than 404 when a sensor ships nothing: the caller renders a
    plain text field in that case, which is still better than nothing.
    """
    definition = catalog.get(name)
    if definition.schema is None:
        return SensorSchemaOut()
    return _schema_out(definition.schema)


@router.post("/{name}/render-parameters", response_model=RenderParametersOut)
async def render_parameters(
    name: str,
    payload: RenderParametersIn,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> RenderParametersOut:
    """Turn form values into the exact line to paste into PRTG."""
    definition = catalog.get(name)
    if definition.schema is None:
        raise ValidationFailedError(
            params={"sensor": name},
            details="this sensor does not ship a parameter schema",
        )
    return RenderParametersOut(
        parameters=render_parameter_line(definition.schema, payload.values)
    )


# --- Variants ---------------------------------------------------------------
#
# A variant is one profile under runtime/sensor-profiles/ plus the files that
# belong to it. Writing it is two steps on purpose: the values are stored
# synchronously, so no credential is ever handed to a job and written into the
# job table, and the job that follows only names the variant and reads it back
# out of the runtime directory.


def _require_schema(definition: SensorDefinition) -> SensorSchema:
    if definition.schema is None or not definition.schema.supports_profiles:
        raise ValidationFailedError(
            params={"sensor": definition.name},
            details="this sensor does not take settings, credentials or files",
        )
    return definition.schema


def _profile_out(record: SensorProfileRecord, schema: SensorSchema) -> SensorProfileOut:
    return SensorProfileOut(
        sensor=record.sensor,
        name=record.name,
        updated_at=record.updated_at,
        probes=list(record.probes),
        files=[
            SensorProfileFileOut(
                key=entry.key,
                filename=entry.filename,
                size_bytes=entry.size_bytes,
                sha256=entry.sha256,
                probe_path=probe_profile_file_path(
                    record.sensor, record.name, entry.filename
                ),
            )
            for entry in record.files
        ],
        parameter_line=profile_parameter_line(schema, record.name),
    )


@router.get("/{name}/profiles", response_model=list[SensorProfileOut])
async def list_profiles(
    name: str,
    catalog: CatalogDep,
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> list[SensorProfileOut]:
    definition = catalog.get(name)
    schema = _require_schema(definition)
    return [
        _profile_out(record, schema)
        for record in runtime.list_sensor_profiles(definition.name)
    ]


@router.get("/{name}/profiles/{profile}", response_model=SensorProfileDetailOut)
async def get_profile(
    name: str,
    profile: str,
    catalog: CatalogDep,
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.SENSOR_READ))],
) -> SensorProfileDetailOut:
    definition = catalog.get(name)
    schema = _require_schema(definition)
    if not runtime.sensor_profile_exists(definition.name, profile):
        raise NotFoundError.of("sensor_profile", f"{name}/{profile}")

    stored = runtime.read_sensor_profile(definition.name, profile)
    record = next(
        (
            entry
            for entry in runtime.list_sensor_profiles(definition.name)
            if entry.name == profile
        ),
        None,
    )
    secret_keys = {field.name for field in schema.credentials}
    base = _profile_out(record, schema) if record else None

    return SensorProfileDetailOut(
        sensor=definition.name,
        name=profile,
        updated_at=base.updated_at if base else None,
        probes=base.probes if base else [],
        files=base.files if base else [],
        parameter_line=profile_parameter_line(schema, profile),
        values={key: value for key, value in stored.items() if key not in secret_keys},
        secrets_set=sorted(key for key in stored if key in secret_keys and stored[key]),
    )


@router.put(
    "/{name}/profiles/{profile}",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def write_profile(
    name: str,
    profile: str,
    payload: SensorProfileIn,
    catalog: CatalogDep,
    runtime: RuntimeDep,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> JobAccepted:
    """Store one variant and hand it to the probes that are meant to hold it."""
    definition = catalog.get(name)
    schema = _require_schema(definition)
    if not NAME_PATTERN.match(profile):
        raise ValidationFailedError(
            params={"profile": profile},
            details="a variant name is letters, digits, dot, dash and underscore",
        )

    values = _merge_values(runtime, definition.name, profile, schema, payload.values)
    # The file paths are generated, never taken from the caller: they are what
    # the helper builds on the probe, and a value from outside would be a way
    # to point a sensor at any file on it.
    for entry in runtime.list_sensor_profile_files(definition.name, profile):
        field = schema.file_field(entry.key)
        if field is not None:
            values[entry.key] = probe_profile_file_path(
                definition.name, profile, entry.filename
            )

    runtime.write_sensor_profile(definition.name, profile, values)

    usernames = [
        (await probes.get_record(probe_id)).nats_username for probe_id in payload.probes
    ]
    job = await jobs.create(
        JobRequest(
            type=sensor_actions.WRITE_PROFILE_JOB_TYPE,
            steps=sensor_actions.WRITE_PROFILE_STEPS,
            target_type="sensor",
            target_label=f"{profile} @ {definition.name}",
            # Names only. The values are in the runtime directory, where the
            # handler reads them from - the job table never sees a credential.
            payload={
                "sensor": definition.name,
                "profile": profile,
                "probes": usernames,
            },
        ),
        principal,
    )
    audit.record(
        action="sensor.profile.write",
        object_type="sensor",
        object_id=definition.name,
        object_label=f"{profile} @ {definition.name}",
        # Field names, never their values.
        after={"fields": sorted(values), "probes": usernames},
        job_id=job.id,
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


def _merge_values(
    runtime: RuntimeFileStore,
    sensor: str,
    profile: str,
    schema: SensorSchema,
    submitted: dict[str, str],
) -> dict[str, str]:
    """The values to store: what was sent, with the secrets that were not.

    An empty credential field means "leave it as it is", not "clear it". The
    interface never receives a stored secret, so it cannot send one back, and
    without this rule every edit of a variant would wipe its password.
    """
    stored = (
        runtime.read_sensor_profile(sensor, profile)
        if runtime.sensor_profile_exists(sensor, profile)
        else {}
    )
    values: dict[str, str] = {}
    for field in schema.profile_fields:
        given = (submitted.get(field.name) or "").strip()
        if given:
            values[field.name] = given
        elif field.sensitive and stored.get(field.name):
            values[field.name] = stored[field.name]

    missing = [
        field.name
        for field in schema.profile_fields
        if field.required and not values.get(field.name)
    ]
    if missing:
        raise ValidationFailedError(
            params={"sensor": sensor, "fields": ", ".join(missing)},
            details=f"required fields are missing: {', '.join(missing)}",
        )
    return values


@router.put(
    "/{name}/profiles/{profile}/files/{key}",
    response_model=SensorProfileFileOut,
)
async def upload_profile_file(
    name: str,
    profile: str,
    key: str,
    payload: SensorProfileFileIn,
    catalog: CatalogDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    _: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> SensorProfileFileOut:
    """Take a certificate or key for one variant.

    Base64 in JSON rather than a multipart upload: it is the same encoding the
    file travels in from here to the probe, so there is one representation on
    the whole path instead of a format change in the middle - and a certificate
    is kilobytes, not a payload that would justify streaming.

    Stored here only; it reaches the probes with the next write of the variant
    or the next rollout of the sensor, which is where the ordering against the
    profile is kept.
    """
    definition = catalog.get(name)
    schema = _require_schema(definition)
    field = schema.file_field(key)
    if field is None:
        raise ValidationFailedError(
            params={"sensor": name, "key": key},
            details="this sensor declares no file under that name",
        )

    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValidationFailedError(
            params={"key": key}, details="the content is not valid base64"
        ) from exc
    if len(content) > field.max_bytes:
        raise ValidationFailedError(
            params={"key": key, "max_bytes": str(field.max_bytes)},
            details=f"the file is larger than {field.max_bytes} bytes",
        )
    if not content:
        raise ValidationFailedError(params={"key": key}, details="the file is empty")

    filename = f"{key}{field.extension}"
    runtime.write_sensor_profile_file(definition.name, profile, key, filename, content)
    audit.record(
        action="sensor.profile.upload",
        object_type="sensor",
        object_id=definition.name,
        object_label=f"{key} of {profile} @ {definition.name}",
        after={"filename": filename, "size_bytes": len(content)},
    )
    return SensorProfileFileOut(
        key=key,
        filename=filename,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        probe_path=probe_profile_file_path(definition.name, profile, filename),
    )


@router.delete(
    "/{name}/profiles/{profile}",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_profile(
    name: str,
    profile: str,
    catalog: CatalogDep,
    runtime: RuntimeDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_CONFIGURE))
    ],
) -> JobAccepted:
    """Remove one variant here and from every probe that holds it."""
    definition = catalog.get(name)
    _require_schema(definition)
    if not runtime.sensor_profile_exists(definition.name, profile):
        raise NotFoundError.of("sensor_profile", f"{name}/{profile}")

    record = next(
        (
            entry
            for entry in runtime.list_sensor_profiles(definition.name)
            if entry.name == profile
        ),
        None,
    )
    usernames = list(record.probes) if record else []

    job = await jobs.create(
        JobRequest(
            type=sensor_actions.REMOVE_PROFILE_JOB_TYPE,
            steps=sensor_actions.REMOVE_PROFILE_STEPS,
            target_type="sensor",
            target_label=f"{profile} @ {definition.name}",
            payload={
                "sensor": definition.name,
                "profile": profile,
                "probes": usernames,
            },
        ),
        principal,
    )
    # Removed from the store right away: the job takes it off the probes, and a
    # variant that stayed here after that would be redeployed by the next
    # rollout of the sensor.
    runtime.remove_sensor_profile(definition.name, profile)
    audit.record(
        action="sensor.profile.delete",
        object_type="sensor",
        object_id=definition.name,
        object_label=f"{profile} @ {definition.name}",
        before={"probes": usernames},
        job_id=job.id,
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )
