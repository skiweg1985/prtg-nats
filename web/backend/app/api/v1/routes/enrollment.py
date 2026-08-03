"""Enrolling a host by invitation.

Two halves with different callers. The token routes are ordinary authenticated
API: an operator creates an invitation and gets a command to paste. The
/enroll/{token}/* routes are reached by the host itself, which has no identity
yet - the token is the whole of its authorisation, which is why it is
single-use, short-lived and revocable.

Those three unauthenticated routes are listed in tests/api/test_permissions.py
with the reason they are exempt, the same way signing in is.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import Field, field_validator

from app.api.deps.common import (
    AuditDep,
    DbSession,
    JobServiceDep,
    PrincipalDep,
    RuntimeDep,
    SettingsDep,
    require_permission,
)
from app.api.schemas.common import ApiModel
from app.core.errors import EnrollmentTokenInvalidError, HostAlreadyEnrolledError
from app.core.permissions import Permission
from app.domain.probe_config import PROBE_NAME_PATTERN
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN
from app.services.enrollment import (
    DEFAULT_TTL_MINUTES,
    PROBE,
    EnrollmentService,
    EnrolmentTarget,
)
from app.services.jobs import JobRequest, ResourceRef
from app.workers.handlers import probe_enrollment

router = APIRouter(tags=["enrollment"])


def get_enrollment_service(db: DbSession, settings: SettingsDep) -> EnrollmentService:
    return EnrollmentService(db, settings)


EnrollmentDep = Annotated[EnrollmentService, Depends(get_enrollment_service)]


class ProbeInvitationIn(ApiModel):
    nats_username: str = Field(min_length=1, max_length=64)
    probe_name: str | None = Field(default=None, max_length=128)
    # The address the platform will use to reach the probe afterwards. Left
    # empty, the callback's source address is used - which is right on a flat
    # network and wrong behind NAT, so the field exists.
    expected_host: str | None = Field(default=None, max_length=255)
    install_package: bool = True
    create_account: bool = True
    ttl_minutes: int = Field(default=DEFAULT_TTL_MINUTES, ge=5, le=1440)

    @field_validator("nats_username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        if not NATS_USERNAME_PATTERN.match(value):
            raise ValueError("invalid NATS account name")
        return value

    @field_validator("probe_name")
    @classmethod
    def _valid_probe_name(cls, value: str | None) -> str | None:
        """Checked here, not when the configuration is rendered.

        The renderer refuses the same names, but by then the invitation has
        been redeemed, the account created and the inventory written - the
        operator would be looking at a half-finished enrolment for a typo.
        No spaces: it is the name PRTG shows and the shell tooling has always
        constrained it this way.
        """
        if value is None:
            return None
        if not PROBE_NAME_PATTERN.match(value):
            raise ValueError(
                "a probe name may hold letters, digits, dot, underscore, "
                "at-sign and hyphen, and has to start with a letter or digit"
            )
        return value


class InvitationOut(ApiModel):
    """The token is in here exactly once, on creation, and never again."""

    id: str
    kind: str
    nats_username: str | None = None
    probe_name: str | None = None
    expected_host: str | None = None
    expires_at: Any
    created_by_name: str | None = None
    redeemed_at: Any = None
    revoked_at: Any = None
    source_ip: str | None = None
    job_id: str | None = None


class IssuedInvitationOut(InvitationOut):
    token: str
    command: str
    ca_sha256: str


class CallbackIn(ApiModel):
    """What the bootstrap script reports once it has done its work."""

    hostname: str = Field(max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    host_keys: list[str] = Field(min_length=1, max_length=16)
    access_installed: bool = False
    package_installed: bool = False


class CallbackOut(ApiModel):
    accepted: bool
    job_id: str | None = None


def _invitation_out(record: Any) -> InvitationOut:
    return InvitationOut(
        id=record.id,
        kind=record.kind,
        nats_username=record.payload.get("nats_username"),
        probe_name=record.payload.get("probe_name"),
        expected_host=record.expected_host,
        expires_at=record.expires_at,
        created_by_name=record.created_by_name,
        redeemed_at=record.redeemed_at,
        revoked_at=record.revoked_at,
        source_ip=record.source_ip,
        job_id=record.job_id,
    )


# --- Operator side ----------------------------------------------------------


@router.post(
    "/probes/enrollment/tokens",
    response_model=IssuedInvitationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_probe_invitation(
    payload: ProbeInvitationIn,
    enrollment: EnrollmentDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.PROBE_CREATE))
    ],
) -> IssuedInvitationOut:
    """Mint an invitation and return the command to run on the probe.

    The account itself is not created here. It is created when the probe
    actually reports in, so an invitation nobody uses leaves nothing behind.
    """
    # Refused here when the address is known, so the operator finds out before
    # walking to a console. The handler checks again with the address the host
    # actually reports from - this field is optional, and the callback's
    # source address is what fills in for it.
    if payload.expected_host:
        claimed = runtime.probe_username_for_host(
            payload.expected_host, excluding=payload.nats_username
        )
        if claimed:
            raise HostAlreadyEnrolledError(
                params={"host": payload.expected_host, "probe": claimed},
                details=(f"{payload.expected_host} is already enrolled as {claimed}"),
            )

    issued = await enrollment.issue(
        EnrolmentTarget(
            kind=PROBE,
            payload={
                "nats_username": payload.nats_username,
                "probe_name": payload.probe_name,
                "install_package": payload.install_package,
                "create_account": payload.create_account,
            },
            expected_host=payload.expected_host,
            ttl_minutes=payload.ttl_minutes,
        ),
        principal,
    )
    _, ca_sha256 = enrollment.ca_material()
    audit.record(
        action="enrollment.token_create",
        object_type="enrollment_token",
        object_id=issued.record.id,
        object_label=payload.nats_username,
        after={"nats_username": payload.nats_username, "kind": PROBE},
    )
    out = _invitation_out(issued.record)
    return IssuedInvitationOut(
        **out.model_dump(),
        token=issued.token,
        command=enrollment.one_liner(issued.token),
        ca_sha256=ca_sha256,
    )


@router.get("/probes/enrollment/tokens", response_model=list[InvitationOut])
async def list_probe_invitations(
    enrollment: EnrollmentDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> list[InvitationOut]:
    return [_invitation_out(record) for record in await enrollment.list_open(PROBE)]


@router.get("/probes/enrollment/tokens/{token_id}", response_model=InvitationOut)
async def read_probe_invitation(
    token_id: str,
    enrollment: EnrollmentDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_READ))],
) -> InvitationOut:
    """One invitation, open or spent.

    This is what the wizard watches while it waits. The list above carries
    open invitations only, and the callback below redeems the invitation in
    the same request that writes its job id - so a caller following the list
    would see the record disappear rather than gain the job it was waiting
    for.
    """
    return _invitation_out(await enrollment.get(token_id, kind=PROBE))


@router.delete(
    "/probes/enrollment/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_probe_invitation(
    token_id: str,
    enrollment: EnrollmentDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.PROBE_CREATE))],
) -> None:
    record = await enrollment.revoke(token_id)
    audit.record(
        action="enrollment.token_revoke",
        object_type="enrollment_token",
        object_id=record.id,
        object_label=record.payload.get("nats_username") or record.id,
    )


# --- The host's side --------------------------------------------------------
# Unauthenticated. The token is the authorisation, and it is single-use,
# expiring and revocable - see tests/api/test_permissions.py::UNGUARDED.


@router.get("/enroll/{token}/bootstrap.sh", response_class=PlainTextResponse)
async def bootstrap_script(token: str, enrollment: EnrollmentDep) -> Response:
    """The script the one-liner pipes into a shell.

    Fetching it does not redeem the invitation: a half-finished run has to be
    retryable without minting a new token. Redemption happens at the callback,
    when the host has something to report.
    """
    record = await enrollment.resolve(token)
    return PlainTextResponse(
        enrollment.render_bootstrap(record, token),
        media_type="text/x-shellscript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/enroll/{token}/asset/{name}")
async def bootstrap_asset(
    token: str, name: str, enrollment: EnrollmentDep
) -> FileResponse:
    """One of a fixed set of files, by name - never by path."""
    record = await enrollment.resolve(token)
    return FileResponse(
        enrollment.asset_path(record.kind, name),
        media_type="text/plain",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/enroll/{token}/callback", response_model=CallbackOut)
async def bootstrap_callback(
    token: str,
    payload: CallbackIn,
    request: Request,
    enrollment: EnrollmentDep,
    jobs: JobServiceDep,
    audit: AuditDep,
) -> CallbackOut:
    """The host reports in; the platform takes over from here.

    Its SSH host keys arrive on this call rather than being scanned from the
    platform. That is the better anchor: only the holder of a valid invitation
    can open this channel, and the operator who pasted the command is, by
    definition, on the intended host.
    """
    record = await enrollment.resolve(token)
    source_ip = request.client.host if request.client else None

    await enrollment.redeem(
        record,
        source_ip=source_ip,
        reported=payload.model_dump(),
    )

    # Where the platform will reach it. The operator's answer wins: the source
    # address is what we see, which is the wrong address exactly when the host
    # is behind NAT.
    host = record.expected_host or source_ip
    if not host:
        raise EnrollmentTokenInvalidError()

    username = record.payload["nats_username"]
    job = await jobs.create(
        JobRequest(
            type=probe_enrollment.ENROLL_JOB_TYPE,
            steps=probe_enrollment.ENROLL_STEPS,
            resources=(
                ResourceRef("credential", username),
                ResourceRef("ssh", "known_hosts"),
            ),
            target_type="probe",
            target_id=username,
            target_label=username,
            payload={
                "nats_username": username,
                "probe_name": record.payload.get("probe_name"),
                "host": host,
                "ssh_port": payload.ssh_port,
                "host_keys": payload.host_keys,
            },
        ),
        # No principal: the host did this, not a person. The audit record
        # below names who issued the invitation.
        None,
    )
    record.job_id = job.id

    audit.record(
        action="enrollment.token_redeem",
        object_type="enrollment_token",
        object_id=record.id,
        object_label=username,
        job_id=job.id,
        comment=f"reported by {payload.hostname} from {source_ip}",
    )
    return CallbackOut(accepted=True, job_id=job.id)
