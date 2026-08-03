"""NATS account management.

What manage-users.sh used to do, with roles, jobs and an audit trail. The
account files stay byte-compatible; only the way they change is new.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import Field, field_validator

from app.api.deps.common import (
    AuditDep,
    DockerDep,
    JobServiceDep,
    PrincipalDep,
    ProbeServiceDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel, JobAccepted
from app.core.errors import NotFoundError
from app.core.permissions import Permission
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN
from app.services.jobs import JobRequest, ResourceRef
from app.services.provisioning import ProvisioningService
from app.workers.handlers import probe_lifecycle

router = APIRouter(prefix="/credentials", tags=["credentials"])


class NatsAccountOut(ApiModel):
    username: str
    is_shared: bool
    has_auth_entry: bool
    # Which enrolled probe uses it, if any - drives the rotate button's hint.
    probe_enrolled: bool


class AccountCreateIn(ApiModel):
    username: str = Field(min_length=1, max_length=64)

    @field_validator("username")
    @classmethod
    def _valid(cls, value: str) -> str:
        if not NATS_USERNAME_PATTERN.match(value):
            raise ValueError("invalid NATS account name")
        return value


class RevealOut(ApiModel):
    username: str
    password: str


def _nats(settings: SettingsDep) -> NatsRuntime:
    return NatsRuntime(settings)


@router.get("", response_model=list[NatsAccountOut])
async def list_accounts(
    settings: SettingsDep,
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_READ))],
) -> list[NatsAccountOut]:
    enrolled = set(runtime.list_probe_usernames())
    return [
        NatsAccountOut(
            username=account.username,
            is_shared=account.is_shared,
            has_auth_entry=account.has_auth_entry,
            probe_enrolled=account.username in enrolled,
        )
        for account in _nats(settings).list_accounts()
    ]


@router.post("", response_model=NatsAccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreateIn,
    settings: SettingsDep,
    docker: DockerDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_ROTATE))],
) -> NatsAccountOut:
    """Create an account and reload the server.

    Synchronous rather than a job: it is filesystem writes plus a SIGHUP, and
    the operator creating it wants to use it immediately. The password is not
    returned - it is revealed explicitly, so the disclosure is its own audited
    decision.
    """
    provisioning = ProvisioningService(settings, docker)
    await provisioning.create_account(payload.username)
    audit.record(
        action="credential.create",
        object_type="nats_account",
        object_id=payload.username,
        object_label=payload.username,
        after={"username": payload.username},
    )
    return NatsAccountOut(
        username=payload.username,
        is_shared=False,
        has_auth_entry=True,
        probe_enrolled=payload.username in set(runtime.list_probe_usernames()),
    )


@router.post(
    "/{username}/rotate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rotate_account(
    username: str,
    settings: SettingsDep,
    runtime: RuntimeDep,
    probes: ProbeServiceDep,
    jobs: JobServiceDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.CREDENTIAL_ROTATE))
    ],
) -> JobAccepted:
    """Rotate as a job: server side first, then the probe is reconfigured over
    the management channel, so both sides change in one operation."""
    nats = _nats(settings)
    if not nats.account_exists(username):
        raise NotFoundError.of("nats_account", username)

    resources = [ResourceRef("credential", username), ResourceRef("nats", "server")]
    if username in set(runtime.list_probe_usernames()):
        # Probe locks are keyed by the record id, same as every other job that
        # touches a probe - two lock vocabularies would never collide.
        record = await probes.ensure_record(username)
        resources.append(ResourceRef("probe", record.id))

    job = await jobs.create(
        JobRequest(
            type=probe_lifecycle.ROTATE_JOB_TYPE,
            steps=probe_lifecycle.ROTATE_STEPS,
            resources=tuple(resources),
            target_type="credential",
            target_id=username,
            target_label=username,
            payload={"probe": username},
        ),
        principal,
    )
    audit.record(
        action="credential.rotate",
        object_type="nats_account",
        object_id=username,
        object_label=username,
        job_id=job.id,
    )
    return JobAccepted(
        job_id=job.id,
        status=job.status.value,
        events_url=f"/api/v1/jobs/{job.id}/events",
    )


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    username: str,
    settings: SettingsDep,
    docker: DockerDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_ROTATE))],
) -> None:
    """Refused while a probe is enrolled for it, and for the last account -
    the same two refusals the shell made."""
    provisioning = ProvisioningService(settings, docker)
    await provisioning.delete_account(username)
    audit.record(
        action="credential.delete",
        object_type="nats_account",
        object_id=username,
        object_label=username,
        before={"username": username},
    )


@router.get("/{username}/reveal", response_model=RevealOut)
async def reveal_password(
    username: str,
    settings: SettingsDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.CREDENTIAL_ROTATE))],
) -> RevealOut:
    """Show the cleartext password.

    Needed exactly once per credential: the shared account goes into the PRTG
    core's settings by hand. Every reveal leaves an audit record of who looked.
    """
    password = _nats(settings).read_password(username)
    audit.record(
        action="credential.reveal",
        object_type="nats_account",
        object_id=username,
        object_label=username,
        comment="cleartext password revealed",
    )
    return RevealOut(username=username, password=password)
