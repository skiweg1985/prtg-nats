"""Sign-in, sign-out and the first-run wizard."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps.common import (
    AuditDep,
    AuthDep,
    DbSession,
    OptionalPrincipalDep,
    PrincipalDep,
    SettingsDep,
    client_ip,
)
from app.api.schemas.auth import (
    AuthStateOut,
    ChangePasswordIn,
    LoginIn,
    PrincipalOut,
    SetupIn,
)
from app.core.errors import AuthenticationRequiredError, SetupRequiredError
from app.domain.enums import AuditResult
from app.persistence.models.identity import WebUser
from app.services.auth import AuthService, Principal

router = APIRouter(prefix="/auth", tags=["auth"])


def _principal_out(
    principal: Principal, *, must_change_password: bool = False
) -> PrincipalOut:
    return PrincipalOut(
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        roles=sorted(principal.roles),
        permissions=sorted(permission.value for permission in principal.permissions),
        locale=principal.locale,
        is_development=principal.is_development,
        must_change_password=must_change_password,
    )


def _set_session_cookie(
    response: Response, settings: SettingsDep, token: str, max_age: int
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        # Lax rather than Strict: the platform is opened from bookmarks and
        # links in tickets, and Strict would drop the session on every one.
        samesite="lax",
        path="/",
    )


@router.get("/state", response_model=AuthStateOut)
async def auth_state(
    auth: AuthDep, settings: SettingsDep, principal: OptionalPrincipalDep
) -> AuthStateOut:
    """The first call the browser makes. Decides login, setup or dashboard."""
    return AuthStateOut(
        authenticated=principal is not None,
        setup_required=not await auth.has_any_user(),
        dev_auth=settings.dev_auth_enabled,
        principal=None if principal is None else _principal_out(principal),
    )


@router.post("/setup", response_model=AuthStateOut, status_code=status.HTTP_201_CREATED)
async def initial_setup(
    payload: SetupIn,
    request: Request,
    response: Response,
    auth: AuthDep,
    settings: SettingsDep,
    audit: AuditDep,
) -> AuthStateOut:
    user = await auth.create_initial_administrator(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
    )
    audit.record(
        action="user.create",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
        after={"username": user.username, "roles": sorted(user.role_names)},
        comment="initial administrator",
        actor_id=user.id,
        actor_name=user.username,
    )
    issued = await auth.issue_session(
        user,
        source_ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(
        response, settings, issued.token, settings.session_lifetime_hours * 3600
    )
    principal = AuthService.principal_for(user)
    return AuthStateOut(
        authenticated=True,
        setup_required=False,
        dev_auth=settings.dev_auth_enabled,
        principal=_principal_out(principal),
    )


@router.post("/login", response_model=AuthStateOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    auth: AuthDep,
    db: DbSession,
    settings: SettingsDep,
    audit: AuditDep,
) -> AuthStateOut:
    if not await auth.has_any_user():
        raise SetupRequiredError()

    source_ip = client_ip(request)
    try:
        user = await auth.authenticate(
            payload.username, payload.password, source_ip=source_ip
        )
    except Exception as exc:
        # A failed sign-in is recorded too: it is the only trace an attempted
        # intrusion leaves. The password never reaches this code path.
        audit.record(
            action="auth.login",
            object_type="web_user",
            object_label=payload.username,
            result=AuditResult.FAILURE,
            error_code=getattr(exc, "code", "auth.invalid_credentials"),
            actor_name=payload.username,
        )
        # Committed here rather than left to the request teardown, which rolls
        # back on an exception. Without this the attempt counter and the audit
        # record die with the failure they exist to remember - and a lock-out
        # that resets on every wrong password protects nothing.
        await db.commit()
        raise

    issued = await auth.issue_session(
        user, source_ip=source_ip, user_agent=request.headers.get("user-agent")
    )
    _set_session_cookie(
        response, settings, issued.token, settings.session_lifetime_hours * 3600
    )
    audit.record(
        action="auth.login",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
        actor_id=user.id,
        actor_name=user.username,
    )
    principal = AuthService.principal_for(user)
    return AuthStateOut(
        authenticated=True,
        setup_required=False,
        dev_auth=settings.dev_auth_enabled,
        principal=_principal_out(
            principal, must_change_password=user.must_change_password
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: AuthDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    audit: AuditDep,
) -> None:
    if principal.session_id:
        await auth.revoke_session(principal.session_id)
    audit.record(
        action="auth.logout",
        object_type="web_user",
        object_id=principal.user_id,
        object_label=principal.username,
    )
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordIn,
    auth: AuthDep,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
) -> None:
    if principal.is_development:
        raise AuthenticationRequiredError(
            details="the development identity has no password"
        )
    user = await db.get(WebUser, principal.user_id)
    if user is None:
        raise AuthenticationRequiredError()
    await auth.change_password(user, payload.current_password, payload.new_password)
    audit.record(
        action="user.change_password",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
    )
