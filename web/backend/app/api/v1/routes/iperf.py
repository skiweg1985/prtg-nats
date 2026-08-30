"""iperf3 measurement endpoints.

Three ways in, because the topology decides which one is possible:

*Push* - the platform signs in once as an administrator and installs the
access. The usual case, and the only one that works when the endpoint cannot
reach this installation, which is most of the time for a host on a public
address.

*Invitation* - the endpoint fetches a bootstrap script and reports in, the way
a probe does. Needs this platform to be reachable from there, so it fits an
endpoint on the same network.

*Registration* - a host somebody else operates. Nothing is installed and
nothing is set up; the record here is the whole of what we have.

All three write the same files. ``./prtg-nats iperf-server`` still reads them,
so an endpoint set up from the browser is one the command line can deploy, show
and revoke without knowing where it came from.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field, field_validator

from app.api.deps.common import (
    AuditDep,
    CatalogDep,
    JobServiceDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel
from app.api.schemas.system import IperfEndpointOut, IperfHolderOut
from app.api.v1.routes.enrollment import valid_source_cidr
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.permissions import Permission
from app.infrastructure.runtime_files import NAME_PATTERN
from app.infrastructure.sensor_catalog import profile_parameter_line
from app.infrastructure.ssh_provisioning import scan_host_keys
from app.services import job_secrets
from app.services.jobs import JobRequest, ResourceRef
from app.workers.handlers import iperf_provisioning
from app.workers.handlers.deploy_sensor import default_endpoint

router = APIRouter(prefix="/iperf-endpoints", tags=["infrastructure"])

_USER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _valid_profile_password(value: str) -> str:
    """Accept only values the line-based runtime/profile format preserves."""
    if not value:
        return value
    if value.strip() != value:
        raise ValueError("an iperf password cannot have surrounding whitespace")
    if any(
        unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value
    ):
        raise ValueError("an iperf password cannot contain line or control characters")
    return value


class JobAccepted(ApiModel):
    job_id: str
    status: str
    events_url: str


class HostKeyScanIn(ApiModel):
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)


class HostKeyOut(ApiModel):
    line: str
    algorithm: str
    fingerprint: str


class HostKeyScanOut(ApiModel):
    host: str
    ssh_port: int
    keys: list[HostKeyOut]
    # True when this host is already pinned and the scan agrees with it. The
    # interface can then say "known" instead of asking for an acceptance that
    # was given once already.
    already_pinned: bool = False


class AdminSignIn(ApiModel):
    """The one-time sign-in. Never stored, never logged, never echoed back."""

    username: str = Field(min_length=1, max_length=64)
    password: str | None = Field(default=None, max_length=1024)
    private_key: str | None = Field(default=None, max_length=32768)
    key_passphrase: str | None = Field(default=None, max_length=1024)
    sudo_password: str | None = Field(default=None, max_length=1024)


class ProvisionIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    iperf_port: int = Field(default=5201, ge=1, le=65535)
    username: str = Field(default="prtg-probe", min_length=1, max_length=32)
    ssh_source_cidr: str | None = Field(default=None, max_length=255)
    # The keys the scan returned and a person accepted. Required: this is the
    # acceptance, and without it the sign-in below would go to whatever answers
    # the address.
    host_keys: list[str] = Field(min_length=1, max_length=16)
    admin: AdminSignIn

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError(
                "an endpoint name may hold letters, digits, dot, underscore "
                "and hyphen, and has to start with a letter or digit"
            )
        return value

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        if not _USER_PATTERN.match(value):
            raise ValueError(
                "an iperf user name may hold letters, digits, hyphen and underscore"
            )
        return value

    @field_validator("ssh_source_cidr")
    @classmethod
    def _valid_cidr(cls, value: str | None) -> str | None:
        return None if value is None else valid_source_cidr(value)


class ProbeSelection(ApiModel):
    """Which probes an endpoint's credentials are handed to, or taken from.

    Named explicitly rather than offering an "all probes" flag. The set is
    small, it is on screen when the choice is made, and a request that names
    its targets is one an audit entry can be read back from a year later.
    """

    probes: list[str] = Field(min_length=1, max_length=512)


class RegisterIn(ApiModel):
    """An endpoint somebody else operates.

    Everything is given rather than discovered, because nothing here is going
    to sign in anywhere. The public key is optional only because an endpoint
    without authentication has none; with a user name it is required, or the
    sensor could not encrypt what it sends and would fail on every run.
    """

    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5201, ge=1, le=65535)
    username: str = Field(default="", max_length=32)
    password: str = Field(default="", max_length=1024)
    public_key_pem: str | None = Field(default=None, max_length=16384)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError(
                "an endpoint name may hold letters, digits, dot, underscore "
                "and hyphen, and has to start with a letter or digit"
            )
        return value

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _valid_profile_password(value)


class ForeignCredentialsIn(ApiModel):
    """A replacement supplied by the operator of a foreign endpoint.

    Write-only by API shape: no response model has this field, and the job
    receives it out of band rather than through its persisted payload.
    """

    password: str = Field(min_length=1, max_length=1024)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _valid_profile_password(value)


def _endpoint_out(endpoint: Any, holders: list[IperfHolderOut]) -> IperfEndpointOut:
    return IperfEndpointOut(
        name=endpoint.name,
        host=endpoint.host,
        port=endpoint.port,
        username=endpoint.username,
        kind=endpoint.kind,
        updated_at=endpoint.updated_at,
        has_public_key=endpoint.has_public_key,
        managed=endpoint.managed,
        holders=sorted(holders, key=lambda holder: holder.probe),
    )


def _endpoint_schema(catalog: Any, kind: str) -> Any:
    """The schema of the sensor that measures against this kind of far end.

    Asked of the catalogue rather than hard-coded, so the option a sensor names
    its profile with is read from its own declaration. None when no sensor
    measures against this kind - the line then falls back to --profile, which
    is what the reader on the probe listens to regardless.
    """
    for definition in catalog.list():
        if definition.iperf_kind == kind:
            return definition.schema
    return None


def _holder_map(runtime: Any, catalog: Any) -> dict[str, list[IperfHolderOut]]:
    """Which probes hold credentials for each endpoint, and what PRTG needs.

    Derived rather than stored: the probes record it themselves, in the sidecar
    the rollout writes, and that file is the only place the answer exists.

    The alias is asked of default_endpoint rather than worked out again here.
    It is the same question the rollout answers when it writes the profile, and
    two implementations of it would mean the interface showing a line that does
    not hold on the probe.
    """
    held: dict[str, list[str]] = {}
    for probe in runtime.read_all_probes():
        for name in probe.known_iperf_endpoints:
            held.setdefault(name, []).append(probe.nats_username)

    registered = {
        endpoint.name: endpoint for endpoint in runtime.list_iperf_endpoints()
    }
    holders: dict[str, list[IperfHolderOut]] = {}
    for name, probes in held.items():
        endpoint = registered.get(name)
        if endpoint is None:
            # A probe remembering an endpoint the registry has forgotten. It is
            # no holder of anything this platform can describe, and counting it
            # would put a line on screen for a host that is gone.
            continue
        schema = _endpoint_schema(catalog, endpoint.kind)
        for username in probes:
            alias = default_endpoint(runtime, username)
            holders.setdefault(name, []).append(
                IperfHolderOut(
                    probe=username,
                    endpoints_held=len(
                        [
                            entry
                            for entry in runtime.assigned_iperf(username)
                            if entry in registered
                        ]
                    ),
                    uses_default_alias=alias == name,
                    parameter_line=(
                        "" if alias == name else profile_parameter_line(schema, name)
                    ),
                )
            )
    return holders


@router.get("", response_model=list[IperfEndpointOut])
async def list_endpoints(
    runtime: RuntimeDep,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> list[IperfEndpointOut]:
    holders = _holder_map(runtime, catalog)
    return [
        _endpoint_out(endpoint, holders.get(endpoint.name, []))
        for endpoint in runtime.list_iperf_endpoints()
    ]


@router.get("/{name}", response_model=IperfEndpointOut)
async def get_endpoint(
    name: str,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> IperfEndpointOut:
    """One endpoint, with the probes holding it.

    Its own route rather than a filter over the list: an endpoint that was
    removed has to answer 404, and a page picking one out of a listing would
    render itself empty instead.
    """
    endpoint = _require_endpoint(runtime, name)
    return _endpoint_out(endpoint, _holder_map(runtime, catalog).get(name, []))


@router.post("/host-keys", response_model=HostKeyScanOut)
async def read_host_keys(
    payload: HostKeyScanIn,
    settings: SettingsDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_MANAGE))],
) -> HostKeyScanOut:
    """The host's SSH keys, without signing in - what ssh-keyscan does.

    Its own step on purpose. The keys have to be seen by a person before any
    administrator credential travels to that address, and the honest moment for
    that is before the first sign-in rather than after it.

    This is the one route that opens a connection to an address the caller
    names, which is why it needs the manage permission rather than the read
    one: it reads nothing here, but it makes this server talk to somewhere.
    """
    from app.infrastructure import known_hosts

    keys = await scan_host_keys(payload.host, payload.ssh_port)
    pinned = known_hosts.read_pinned(
        settings.ssh_known_hosts_path, payload.host, payload.ssh_port
    )
    offered = {key.blob for key in keys}
    return HostKeyScanOut(
        host=payload.host,
        ssh_port=payload.ssh_port,
        keys=[
            HostKeyOut(
                line=str(key), algorithm=key.algorithm, fingerprint=key.fingerprint
            )
            for key in keys
        ],
        already_pinned=bool(pinned) and all(key.blob in offered for key in pinned),
    )


@router.post("", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def provision_endpoint(
    payload: ProvisionIn,
    request: Request,
    runtime: RuntimeDep,
    settings: SettingsDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.IPERF_MANAGE))
    ],
) -> JobAccepted:
    """Set an endpoint up by signing in to it once.

    The administrator credentials never reach the job payload, which is a row
    in the database. They are handed over out of band and taken by the worker
    when it starts - see app/services/job_secrets.py.
    """
    if runtime.iperf_endpoint_exists(payload.name):
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": payload.name},
            details=f"an endpoint named {payload.name} is already registered",
        )
    if not payload.admin.password and not payload.admin.private_key:
        raise ValidationFailedError(
            params={"field": "admin"},
            details="a password or a private key is needed to sign in",
        )

    source_cidr = payload.ssh_source_cidr or (
        runtime.site_settings().iperf_ssh_source_cidr or ""
    )
    if not source_cidr:
        raise ValidationFailedError(
            params={"field": "ssh_source_cidr"},
            details="no source network for the endpoint: set one here, or "
            "IPERF_SSH_SOURCE_CIDR for the site",
        )

    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.PROVISION_JOB_TYPE,
            steps=iperf_provisioning.PROVISION_STEPS,
            resources=(
                ResourceRef("iperf", payload.name),
                ResourceRef("ssh", "known_hosts"),
            ),
            target_type="iperf_endpoint",
            target_id=payload.name,
            target_label=payload.name,
            payload={
                "name": payload.name,
                "host": payload.host,
                "ssh_port": payload.ssh_port,
                "iperf_port": payload.iperf_port,
                "username": payload.username,
                "ssh_source_cidr": source_cidr,
                "host_keys": payload.host_keys,
            },
        ),
        principal,
    )
    job_secrets.hand(
        job.id,
        {
            key: value
            for key, value in {
                "admin_username": payload.admin.username,
                "admin_password": payload.admin.password or "",
                "admin_private_key": payload.admin.private_key or "",
                "admin_key_passphrase": payload.admin.key_passphrase or "",
                "sudo_password": payload.admin.sudo_password or "",
            }.items()
            if value
        },
    )
    audit.record(
        action="iperf.provision",
        object_type="iperf_endpoint",
        object_id=payload.name,
        object_label=payload.name,
        job_id=job.id,
        # The administrator name, never what authenticated it.
        after={
            "host": payload.host,
            "admin": payload.admin.username,
            "ssh_source_cidr": source_cidr,
        },
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/register", response_model=IperfEndpointOut, status_code=status.HTTP_201_CREATED
)
async def register_endpoint(
    payload: RegisterIn,
    runtime: RuntimeDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_MANAGE))],
) -> IperfEndpointOut:
    """Record an endpoint somebody else operates.

    No job: nothing is installed and nothing is reached. What this writes is
    the same record the other two ways produce, so everything downstream - the
    rollout to probes, the endpoint list, the sensor's profile - treats it
    identically. Only rotation and removal know the difference, because neither
    is ours to perform.
    """
    if runtime.iperf_endpoint_exists(payload.name):
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": payload.name},
            details=f"an endpoint named {payload.name} is already registered",
        )
    # Authentication is all or nothing. A user name without a password means a
    # sensor that fails on every run with "credentials-unreadable", and a
    # password without a key means one that cannot encrypt it.
    if payload.username and not payload.password:
        raise ValidationFailedError(
            params={"field": "password"},
            details="an endpoint with a user name needs its password",
        )
    if payload.username and not payload.public_key_pem:
        raise ValidationFailedError(
            params={"field": "public_key_pem"},
            details="iperf3 encrypts the credentials with the endpoint's public "
            "key; without it the probes cannot authenticate",
        )
    if payload.public_key_pem and "-----BEGIN PUBLIC KEY-----" not in (
        payload.public_key_pem
    ):
        raise ValidationFailedError(
            params={"field": "public_key_pem"},
            details="expected a PEM public key",
        )

    runtime.write_iperf_record(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=payload.password,
        public_key_pem=payload.public_key_pem,
        managed=False,
    )
    audit.record(
        action="iperf.register",
        object_type="iperf_endpoint",
        object_id=payload.name,
        object_label=payload.name,
        after={"host": payload.host, "port": payload.port, "managed": False},
    )
    written = next(
        (
            endpoint
            for endpoint in runtime.list_iperf_endpoints()
            if endpoint.name == payload.name
        ),
        None,
    )
    if written is None:
        raise NotFoundError.of("iperf_endpoint", payload.name)
    return _endpoint_out(written, [])


@router.put(
    "/{name}/credentials",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_foreign_credentials(
    name: str,
    payload: ForeignCredentialsIn,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    probe_service: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.IPERF_MANAGE))
    ],
) -> JobAccepted:
    """Replace our copy of a foreign endpoint password and redeploy it.

    The endpoint's operator has already changed the far side. This action
    therefore touches only the protected runtime record and probes that already
    hold it. The password is handed to the worker in memory: the job row,
    response and audit record describe the rollout without retaining it.
    """
    endpoint = _require_endpoint(runtime, name)
    if endpoint.managed:
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": name},
            details=f"{name} is managed here; rotate its password instead",
        )
    if not endpoint.username or not endpoint.has_public_key:
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": name},
            details=f"{name} has no authenticated credential set to update",
        )

    probes = runtime.read_all_probes()
    holders = sorted(
        probe.nats_username for probe in probes if name in probe.known_iperf_endpoints
    )
    # A deploy that was queued first may turn a known probe into a holder
    # before this job acquires the endpoint lock. Lock every probe that can
    # enter that set, then derive the authoritative holders inside the worker.
    records = [
        await probe_service.ensure_record(probe.nats_username) for probe in probes
    ]
    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.FOREIGN_CREDENTIALS_JOB_TYPE,
            steps=iperf_provisioning.FOREIGN_CREDENTIALS_STEPS,
            resources=(
                ResourceRef("iperf", name),
                *(ResourceRef("probe", record.id) for record in records),
            ),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=f"{name} → {len(holders)} probe(s)",
            payload={
                "name": name,
                "probes": holders,
                "sensors": _endpoint_sensors(catalog),
            },
        ),
        principal,
    )
    job_secrets.hand(job.id, {"iperf_password": payload.password})
    audit.record(
        action="iperf.foreign_credentials_update",
        object_type="iperf_endpoint",
        object_id=name,
        object_label=name,
        job_id=job.id,
        after={"probes": holders},
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/{name}/rotate", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def rotate_password(
    name: str,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.IPERF_MANAGE))
    ],
) -> JobAccepted:
    """Give the endpoint a new password and carry it to the probes.

    Both halves in one job on purpose. Every probe holding this endpoint is
    locked out from the moment the endpoint accepts the new password, so
    refreshing them is not a follow-up someone might forget - it is the repair
    of the state this job just created.
    """
    endpoint = _require_endpoint(runtime, name)
    if not endpoint.managed:
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": name},
            details=f"{name} is operated elsewhere; its password is not ours to change",
        )

    deployed = [
        probe.nats_username
        for probe in runtime.read_all_probes()
        if name in probe.known_iperf_endpoints
    ]
    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.ROTATE_JOB_TYPE,
            steps=iperf_provisioning.ROTATE_STEPS,
            resources=(ResourceRef("iperf", name),),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=name,
            payload={
                "name": name,
                "host": endpoint.host,
                "ssh_port": endpoint.ssh_port,
                "iperf_port": endpoint.port,
                "username": endpoint.username,
                "probes": deployed,
                "sensors": _endpoint_sensors(catalog),
            },
        ),
        principal,
    )
    audit.record(
        action="iperf.rotate",
        object_type="iperf_endpoint",
        object_id=name,
        object_label=name,
        job_id=job.id,
        after={"probes": len(deployed)},
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/{name}/deploy", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def deploy_to_probes(
    name: str,
    payload: ProbeSelection,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    probe_service: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_DEPLOY))
    ],
) -> JobAccepted:
    """Hand this endpoint's credentials to the probes that were named.

    Without this the only way to widen what a probe measures against was to
    roll the whole sensor out again - and the interface could not narrow it at
    all, which left "revoke" as the one operation that needed a terminal.
    """
    _require_endpoint(runtime, name)
    probes = _known_probes(runtime, payload.probes)
    records = [await probe_service.ensure_record(probe) for probe in probes]

    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.DEPLOY_JOB_TYPE,
            steps=iperf_provisioning.DEPLOY_STEPS,
            resources=(
                ResourceRef("iperf", name),
                *(ResourceRef("probe", record.id) for record in records),
            ),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=f"{name} → {len(probes)} probe(s)",
            payload={
                "name": name,
                "probes": probes,
                "sensors": _endpoint_sensors(catalog),
            },
        ),
        principal,
    )
    audit.record(
        action="iperf.deploy",
        object_type="iperf_endpoint",
        object_id=name,
        object_label=name,
        job_id=job.id,
        after={"probes": probes},
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.post(
    "/{name}/revoke", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def revoke_from_probes(
    name: str,
    payload: ProbeSelection,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    probe_service: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.SENSOR_DEPLOY))
    ],
) -> JobAccepted:
    """Take this endpoint's credentials off the probes that were named.

    The endpoint itself is not touched: it keeps running for everybody else,
    and the record here stays. Only these probes stop measuring against it -
    and stay stopped, because the rollout reads the same assignment.
    """
    _require_endpoint(runtime, name)
    probes = _known_probes(runtime, payload.probes)
    records = [await probe_service.ensure_record(probe) for probe in probes]

    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.REVOKE_JOB_TYPE,
            steps=iperf_provisioning.REVOKE_STEPS,
            resources=(
                ResourceRef("iperf", name),
                *(ResourceRef("probe", record.id) for record in records),
            ),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=f"{name} ← {len(probes)} probe(s)",
            payload={
                "name": name,
                "probes": probes,
                "sensors": _endpoint_sensors(catalog),
            },
        ),
        principal,
    )
    audit.record(
        action="iperf.revoke",
        object_type="iperf_endpoint",
        object_id=name,
        object_label=name,
        job_id=job.id,
        after={"probes": probes},
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.delete(
    "/{name}", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def remove_endpoint(
    name: str,
    runtime: RuntimeDep,
    catalog: CatalogDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.IPERF_MANAGE))
    ],
    keep_service: Annotated[
        bool,
        Query(
            description="Leave the iperf3 service running and only forget the "
            "endpoint here"
        ),
    ] = False,
) -> JobAccepted:
    """Take an endpoint off the probes, off its host, and out of the record.

    The iperf3 package is not uninstalled - something else on that host may be
    using it. What goes is the authentication, the key pair and the credentials
    this platform put there, and the access that did the work removes itself
    last.
    """
    endpoint = _require_endpoint(runtime, name)
    deployed = [
        probe.nats_username
        for probe in runtime.read_all_probes()
        if name in probe.known_iperf_endpoints
    ]
    job = await jobs.create(
        JobRequest(
            type=iperf_provisioning.REMOVE_JOB_TYPE,
            steps=iperf_provisioning.REMOVE_STEPS,
            resources=(
                ResourceRef("iperf", name),
                ResourceRef("ssh", "known_hosts"),
            ),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=name,
            payload={
                "name": name,
                "host": endpoint.host,
                "ssh_port": endpoint.ssh_port,
                # An endpoint operated elsewhere is only forgotten here, and so
                # is one the operator wants left running.
                "managed": endpoint.managed and not keep_service,
                "probes": deployed,
                "sensors": _endpoint_sensors(catalog),
            },
        ),
        principal,
    )
    audit.record(
        action="iperf.remove",
        object_type="iperf_endpoint",
        object_id=name,
        object_label=name,
        job_id=job.id,
        before={"host": endpoint.host, "managed": endpoint.managed},
        after={"keep_service": keep_service, "probes": len(deployed)},
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


def _require_endpoint(runtime: Any, name: str) -> Any:
    for endpoint in runtime.list_iperf_endpoints():
        if endpoint.name == name:
            return endpoint
    raise NotFoundError.of("iperf_endpoint", name)


def _known_probes(runtime: Any, requested: list[str]) -> list[str]:
    """The named probes, checked against the inventory and de-duplicated.

    A name that is not enrolled is refused rather than skipped: the request
    said what it wanted, and a job that quietly did less than it was asked
    leaves nobody to notice the typo.
    """
    enrolled = {probe.nats_username for probe in runtime.read_all_probes()}
    unknown = sorted(set(requested) - enrolled)
    if unknown:
        raise NotFoundError.of("probe", ", ".join(unknown))
    return sorted(set(requested))


def _endpoint_sensors(catalog: Any) -> list[str]:
    """Which sensors carry credentials for an endpoint of this kind.

    Asked of the catalogue rather than hard-coded, the same way the shell path
    reads SENSOR_IPERF from the manifest: a second sensor measuring against the
    same kind of far end should be served without editing this.
    """
    return [definition.name for definition in catalog.list() if definition.iperf_kind]
