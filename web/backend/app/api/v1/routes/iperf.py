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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field, field_validator

from app.api.deps.common import (
    AuditDep,
    CatalogDep,
    JobServiceDep,
    PrincipalDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel
from app.api.schemas.system import IperfEndpointOut
from app.api.v1.routes.enrollment import valid_source_cidr
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.permissions import Permission
from app.infrastructure.runtime_files import NAME_PATTERN
from app.infrastructure.ssh_provisioning import scan_host_keys
from app.services import job_secrets
from app.services.jobs import JobRequest, ResourceRef
from app.workers.handlers import iperf_provisioning

router = APIRouter(prefix="/iperf-endpoints", tags=["infrastructure"])

_USER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


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


def _endpoint_out(endpoint: Any, deployed: list[str]) -> IperfEndpointOut:
    return IperfEndpointOut(
        name=endpoint.name,
        host=endpoint.host,
        port=endpoint.port,
        username=endpoint.username,
        kind=endpoint.kind,
        updated_at=endpoint.updated_at,
        has_public_key=endpoint.has_public_key,
        managed=endpoint.managed,
        deployed_to=sorted(deployed),
    )


def _deployment_map(runtime: Any) -> dict[str, list[str]]:
    """Which probes hold credentials for each endpoint.

    Derived rather than stored: the probes record it themselves, in the sidecar
    the rollout writes, and that file is the only place the answer exists.
    """
    deployed: dict[str, list[str]] = {}
    for probe in runtime.read_all_probes():
        for name in probe.known_iperf_endpoints:
            deployed.setdefault(name, []).append(probe.nats_username)
    return deployed


@router.get("", response_model=list[IperfEndpointOut])
async def list_endpoints(
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> list[IperfEndpointOut]:
    deployed = _deployment_map(runtime)
    return [
        _endpoint_out(endpoint, deployed.get(endpoint.name, []))
        for endpoint in runtime.list_iperf_endpoints()
    ]


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

    deployed = _deployment_map(runtime).get(name, [])
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
    deployed = _deployment_map(runtime).get(name, [])
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


def _endpoint_sensors(catalog: Any) -> list[str]:
    """Which sensors carry credentials for an endpoint of this kind.

    Asked of the catalogue rather than hard-coded, the same way the shell path
    reads SENSOR_IPERF from the manifest: a second sensor measuring against the
    same kind of far end should be served without editing this.
    """
    return [definition.name for definition in catalog.list() if definition.iperf_kind]
