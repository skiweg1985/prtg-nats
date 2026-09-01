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

import ipaddress
import re
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
from app.core.errors import (
    ConflictError,
    EnrollmentTokenInvalidError,
    HostAlreadyEnrolledError,
    RuntimeStateError,
)
from app.core.permissions import Permission
from app.domain.probe_config import PROBE_NAME_PATTERN
from app.infrastructure.overlay import OverlayRuntime
from app.infrastructure.runtime_files import NAME_PATTERN, NATS_USERNAME_PATTERN
from app.services.enrollment import (
    DEFAULT_TTL_MINUTES,
    IPERF,
    PROBE,
    EnrollmentService,
    EnrolmentTarget,
)
from app.services.jobs import JobRequest, ResourceRef
from app.workers.handlers import iperf_enrollment, probe_enrollment

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
    # For a probe that cannot reach this platform at all - a site with no
    # site-to-site tunnel. The bootstrap then builds the overlay before its
    # first request instead of after the package, and the peer is reserved
    # here rather than learned from the callback. It also means the script
    # carries a private key, so it is asked for rather than assumed (ADR 0010).
    overlay_bootstrap: bool = False
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


class EnrolmentStepOut(ApiModel):
    """One command to run on the probe before the one-liner works."""

    key: str
    command: str
    carries_secret: bool = False


class IssuedInvitationOut(InvitationOut):
    token: str
    command: str
    # What has to happen on the probe first, for an enrolment over the tunnel:
    # install wireguard-tools, build the tunnel. Empty for every other
    # invitation, where the one-liner alone is the whole ceremony.
    setup_steps: list[EnrolmentStepOut] = Field(default_factory=list)
    ca_sha256: str


def valid_source_cidr(value: str) -> str:
    """A comma separated list of networks, each one checked on its own.

    The same rule libexec/iperf-enroll.sh applies before it writes the key, and
    checked here as well because the script refuses on a console the operator
    has already walked away from. An empty element would widen "from=" to
    something nobody meant.

    Two shapes are handled rather than passed through:

    A bare address gains its host prefix. Somebody typing one address means
    that address, and the enrolment script insists on a prefix - so the
    friendly reading happens here instead of failing three steps later.

    Host bits inside a prefix are refused instead of being masked away.
    ``192.0.2.5/24`` masks to ``192.0.2.0/24``, which is a rule 254 addresses
    wider than what was typed. Widening a management key's reach is not a
    correction to make on somebody's behalf.
    """
    elements = [element.strip() for element in value.split(",")]
    if not elements or any(not element for element in elements):
        raise ValueError("empty network in the list")

    normalised: list[str] = []
    for element in elements:
        if "/" not in element:
            try:
                address = ipaddress.ip_address(element)
            except ValueError as exc:
                raise ValueError(
                    f"{element} is not an address or a network in CIDR notation"
                ) from exc
            normalised.append(f"{address}/{address.max_prefixlen}")
            continue
        try:
            network = ipaddress.ip_network(element, strict=True)
        except ValueError as exc:
            raise ValueError(
                f"{element} is not a network in CIDR notation, or names a "
                "single host inside a wider one"
            ) from exc
        normalised.append(str(network))
    return ",".join(normalised)


class IperfInvitationIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    # The address the probes will measure against, and the one this platform
    # will reach over SSH. Left empty, the callback's source address fills in -
    # right on a flat network and wrong behind NAT, so the field exists.
    expected_host: str | None = Field(default=None, max_length=255)
    iperf_port: int = Field(default=5201, ge=1, le=65535)
    username: str = Field(default="prtg-probe", min_length=1, max_length=32)
    # Which network the endpoint will accept this platform from. Optional here,
    # required by the time the script is rendered: the site default fills in,
    # and if there is none the invitation is refused while somebody is looking
    # at it rather than on the endpoint's console.
    ssh_source_cidr: str | None = Field(default=None, max_length=255)
    ttl_minutes: int = Field(default=DEFAULT_TTL_MINUTES, ge=5, le=1440)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        # This name is also the profile name the credentials carry on every
        # probe, which is why it is this narrow.
        if not NAME_PATTERN.match(value):
            raise ValueError(
                "an endpoint name may hold letters, digits, dot, underscore "
                "and hyphen, and has to start with a letter or digit"
            )
        return value

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError(
                "an iperf user name may hold letters, digits, hyphen and underscore"
            )
        return value

    @field_validator("ssh_source_cidr")
    @classmethod
    def _valid_cidr(cls, value: str | None) -> str | None:
        return None if value is None else valid_source_cidr(value)


class IperfInvitationOut(ApiModel):
    id: str
    kind: str
    name: str | None = None
    expected_host: str | None = None
    iperf_port: int | None = None
    username: str | None = None
    ssh_source_cidr: str | None = None
    expires_at: Any
    created_by_name: str | None = None
    redeemed_at: Any = None
    revoked_at: Any = None
    source_ip: str | None = None
    job_id: str | None = None


class IssuedIperfInvitationOut(IperfInvitationOut):
    token: str
    command: str
    ca_sha256: str


class IperfCallbackIn(ApiModel):
    """What the endpoint bootstrap reports once it has done its work.

    Shorter than the probe's: there is no package to install here, and the
    endpoint itself is set up afterwards over the channel this run installed.
    """

    hostname: str = Field(max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    host_keys: list[str] = Field(min_length=1, max_length=16)
    access_installed: bool = False
    # The address the endpoint reached this platform under. It is the answer to
    # the question the platform cannot answer for itself behind NAT, and the
    # next invitation is filled in with it instead of a guess.
    platform_address: str | None = Field(default=None, max_length=64)


class CallbackIn(ApiModel):
    """What the bootstrap script reports once it has done its work."""

    hostname: str = Field(max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    host_keys: list[str] = Field(min_length=1, max_length=16)
    access_installed: bool = False
    package_installed: bool = False
    # Why the package never made it, in the installer's own words. The
    # bootstrap reports back even when that step failed, and without this the
    # reason dies on a console the operator has already walked away from.
    # Generous rather than tight: a truncated cause is a cause nobody can act
    # on, and the bootstrap already caps what it sends.
    package_error: str | None = Field(default=None, max_length=8192)
    # The public half of a key the probe generated for itself. Absent when the
    # installation has no overlay, and also when it has one and the probe could
    # not join it - which is not a failed enrolment, only a probe with one path
    # instead of two.
    overlay_public_key: str | None = Field(default=None, max_length=64)


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
    settings: SettingsDep,
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

    # One open invitation per account. Two of them redeemed on two hosts would
    # let the second overwrite the first host's inventory under the same name -
    # and the operator holding the older command would never learn why.
    for record in await enrollment.list_open(PROBE):
        if record.payload.get("nats_username") == payload.nats_username:
            raise ConflictError(
                params={
                    "resource": "enrollment_token",
                    "name": payload.nats_username,
                },
                details=(
                    f"an open invitation for {payload.nats_username} already"
                    " exists; revoke it or let it expire"
                ),
            )

    # Refused here rather than producing a script that cannot work: without an
    # overlay there is no tunnel to enrol over, and the probe this was asked
    # for is one nobody can reach to find out.
    if payload.overlay_bootstrap and not OverlayRuntime(settings).settings().enabled:
        raise RuntimeStateError(
            details=("enrolling over the tunnel needs the overlay; turn it on first")
        )

    issued = await enrollment.issue(
        EnrolmentTarget(
            kind=PROBE,
            payload={
                "nats_username": payload.nats_username,
                "probe_name": payload.probe_name,
                "install_package": payload.install_package,
                "overlay_bootstrap": payload.overlay_bootstrap,
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
    # The one-liner is the same ceremony either way. A tunnel enrolment only
    # needs two commands run before it, so that it has a path to fetch over -
    # and it addresses the platform by IP, because the site has no name server
    # that knows it. See ADR 0010.
    setup_steps = [
        EnrolmentStepOut(
            key=step.key,
            command=step.command,
            carries_secret=step.carries_secret,
        )
        for step in (
            enrollment.tunnel_setup_steps(issued.record)
            if payload.overlay_bootstrap
            else []
        )
    ]
    return IssuedInvitationOut(
        **out.model_dump(),
        token=issued.token,
        command=enrollment.one_liner(
            issued.token, by_address=payload.overlay_bootstrap
        ),
        setup_steps=setup_steps,
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


# --- Operator side: iperf endpoints -----------------------------------------


def _iperf_invitation_out(record: Any) -> IperfInvitationOut:
    return IperfInvitationOut(
        id=record.id,
        kind=record.kind,
        name=record.payload.get("name"),
        expected_host=record.expected_host,
        iperf_port=record.payload.get("iperf_port"),
        username=record.payload.get("username"),
        ssh_source_cidr=record.payload.get("ssh_source_cidr"),
        expires_at=record.expires_at,
        created_by_name=record.created_by_name,
        redeemed_at=record.redeemed_at,
        revoked_at=record.revoked_at,
        source_ip=record.source_ip,
        job_id=record.job_id,
    )


@router.post(
    "/iperf-endpoints/enrollment/tokens",
    response_model=IssuedIperfInvitationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_iperf_invitation(
    payload: IperfInvitationIn,
    enrollment: EnrollmentDep,
    runtime: RuntimeDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.IPERF_MANAGE))
    ],
) -> IssuedIperfInvitationOut:
    """Mint an invitation for a measurement endpoint and return the command.

    Nothing is set up here and nothing secret is minted: the password is
    generated when the endpoint actually reports in, so an invitation nobody
    uses leaves nothing behind.
    """
    # The name is also the profile name the credentials carry on every probe.
    # Two endpoints under one name would overwrite each other's credentials on
    # every probe that measures against both.
    if runtime.iperf_endpoint_exists(payload.name):
        raise ConflictError(
            params={"resource": "iperf_endpoint", "name": payload.name},
            details=f"an endpoint named {payload.name} is already registered",
        )

    issued = await enrollment.issue(
        EnrolmentTarget(
            kind=IPERF,
            payload={
                "name": payload.name,
                "iperf_port": payload.iperf_port,
                "username": payload.username,
                "ssh_source_cidr": payload.ssh_source_cidr,
            },
            expected_host=payload.expected_host,
            ttl_minutes=payload.ttl_minutes,
        ),
        principal,
    )
    # Rendered once here, before the operator walks anywhere: without a source
    # network - on the invitation or for the site - the script cannot be built
    # at all, and finding that out on the endpoint's console is finding it out
    # too late.
    enrollment.iperf_source_cidr(issued.record)

    _, ca_sha256 = enrollment.ca_material()
    audit.record(
        action="enrollment.token_create",
        object_type="enrollment_token",
        object_id=issued.record.id,
        object_label=payload.name,
        after={"name": payload.name, "kind": IPERF},
    )
    out = _iperf_invitation_out(issued.record)
    return IssuedIperfInvitationOut(
        **out.model_dump(),
        token=issued.token,
        command=enrollment.one_liner(issued.token, script="iperf-bootstrap.sh"),
        ca_sha256=ca_sha256,
    )


@router.get(
    "/iperf-endpoints/enrollment/tokens", response_model=list[IperfInvitationOut]
)
async def list_iperf_invitations(
    enrollment: EnrollmentDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> list[IperfInvitationOut]:
    return [
        _iperf_invitation_out(record) for record in await enrollment.list_open(IPERF)
    ]


@router.get(
    "/iperf-endpoints/enrollment/tokens/{token_id}",
    response_model=IperfInvitationOut,
)
async def read_iperf_invitation(
    token_id: str,
    enrollment: EnrollmentDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> IperfInvitationOut:
    """One invitation, open or spent - what the wizard watches while it waits."""
    return _iperf_invitation_out(await enrollment.get(token_id, kind=IPERF))


@router.delete(
    "/iperf-endpoints/enrollment/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_iperf_invitation(
    token_id: str,
    enrollment: EnrollmentDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_MANAGE))],
) -> None:
    record = await enrollment.revoke(token_id)
    audit.record(
        action="enrollment.token_revoke",
        object_type="enrollment_token",
        object_id=record.id,
        object_label=record.payload.get("name") or record.id,
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

    Bound to the probe kind now that there is a second one. An endpoint's
    invitation must not be able to fetch this script: it would install the
    probe's management user, with the probe's rights, on a host that only
    measures.
    """
    record = await enrollment.resolve(token, kind=PROBE)
    return PlainTextResponse(
        enrollment.render_bootstrap(record, token),
        media_type="text/x-shellscript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/enroll/{token}/iperf-bootstrap.sh", response_class=PlainTextResponse)
async def iperf_bootstrap_script(token: str, enrollment: EnrollmentDep) -> Response:
    """The endpoint's counterpart to bootstrap.sh.

    Kept as its own route rather than switching on the token's kind inside the
    one above: the two scripts install different access on different hosts, and
    a wrong guess would put a probe's management user on a measurement endpoint.
    """
    record = await enrollment.resolve(token, kind=IPERF)
    return PlainTextResponse(
        enrollment.render_iperf_bootstrap(record, token),
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
    record = await enrollment.resolve(token, kind=PROBE)
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
                "package_error": payload.package_error,
                "overlay_address": record.payload.get("overlay_address"),
                "overlay_mode": record.payload.get("overlay_mode"),
                "overlay_public_key": payload.overlay_public_key,
                # "invitation_id", not "..._token_id": redaction masks any
                # key that reads like a secret, and this one is an id the
                # handler has to be able to use. A masked value reached it as
                # a validation failure two steps later, which is a puzzling
                # way to learn about a naming rule.
                "invitation_id": record.id,
                "overlay_bootstrap": bool(record.payload.get("overlay_bootstrap")),
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


@router.post("/enroll/{token}/iperf-callback", response_model=CallbackOut)
async def iperf_bootstrap_callback(
    token: str,
    payload: IperfCallbackIn,
    request: Request,
    enrollment: EnrollmentDep,
    jobs: JobServiceDep,
    audit: AuditDep,
) -> CallbackOut:
    """The endpoint reports in; the platform sets it up from here.

    Unlike the probe's callback, nothing has been configured on that host yet -
    only the channel exists. Everything the endpoint will hold is decided on
    this side and travels over that channel, which is what makes the channel
    the thing that gets exercised first.
    """
    record = await enrollment.resolve(token, kind=IPERF)
    source_ip = request.client.host if request.client else None

    await enrollment.redeem(record, source_ip=source_ip, reported=payload.model_dump())

    # The operator's answer wins over what we see: the source address is the
    # wrong one exactly when the endpoint is behind NAT, and this address is
    # what the probes will measure against, not just what we reach over SSH.
    host = record.expected_host or source_ip
    if not host:
        raise EnrollmentTokenInvalidError()

    name = record.payload["name"]
    job = await jobs.create(
        JobRequest(
            type=iperf_enrollment.ENROLL_JOB_TYPE,
            steps=iperf_enrollment.ENROLL_STEPS,
            resources=(
                ResourceRef("iperf", name),
                ResourceRef("ssh", "known_hosts"),
            ),
            target_type="iperf_endpoint",
            target_id=name,
            target_label=name,
            payload={
                "name": name,
                "host": host,
                "ssh_port": payload.ssh_port,
                "iperf_port": record.payload.get("iperf_port", 5201),
                "username": record.payload.get("username", "prtg-probe"),
                "host_keys": payload.host_keys,
                "ssh_source_cidr": record.payload.get("ssh_source_cidr"),
            },
        ),
        # No principal: the host did this, not a person. The audit record below
        # names who issued the invitation.
        None,
    )
    record.job_id = job.id

    audit.record(
        action="enrollment.token_redeem",
        object_type="enrollment_token",
        object_id=record.id,
        object_label=name,
        job_id=job.id,
        comment=(
            f"reported by {payload.hostname} from {source_ip}"
            + (
                f", reaching us at {payload.platform_address}"
                if payload.platform_address
                else ""
            )
        ),
    )
    return CallbackOut(accepted=True, job_id=job.id)
