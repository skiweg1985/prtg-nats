"""Web account administration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps.common import (
    AuditDep,
    AuthDep,
    DbSession,
    PrincipalDep,
    require_permission,
)
from app.api.schemas.auth import UserCreateIn, UserOut, UserUpdateIn
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.permissions import Permission, RoleName
from app.persistence.models.identity import UserRole, WebUser

router = APIRouter(prefix="/users", tags=["users"])

VALID_ROLES = {role.value for role in RoleName}


def _out(user: WebUser) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=sorted(user.role_names),
        is_active=user.is_active,
        locale=user.locale,
        last_login_at=user.last_login_at,
        locked_until=user.locked_until,
        created_at=user.created_at,
    )


def _validate_roles(roles: list[str]) -> set[str]:
    unknown = set(roles) - VALID_ROLES
    if unknown:
        raise ValidationFailedError(
            fields=["roles"],
            params={"unknown": sorted(unknown)},
            details=f"unknown roles: {', '.join(sorted(unknown))}",
        )
    return set(roles)


@router.get("", response_model=list[UserOut])
async def list_users(
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.USER_MANAGE))],
) -> list[UserOut]:
    users = await db.scalars(select(WebUser).order_by(WebUser.username_folded))
    return [_out(user) for user in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateIn,
    auth: AuthDep,
    audit: AuditDep,
    _: Annotated[object, Depends(require_permission(Permission.USER_MANAGE))],
) -> UserOut:
    roles = _validate_roles(payload.roles)
    user = await auth.create_user(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        email=payload.email,
        roles=roles,
        must_change_password=payload.must_change_password,
    )
    audit.record(
        action="user.create",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
        after={"username": user.username, "roles": sorted(roles)},
    )
    return _out(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdateIn,
    db: DbSession,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.USER_MANAGE))
    ],
) -> UserOut:
    user = await db.get(WebUser, user_id)
    if user is None:
        raise NotFoundError.of("web_user", user_id)

    before = {
        "display_name": user.display_name,
        "roles": sorted(user.role_names),
        "is_active": user.is_active,
    }

    if payload.roles is not None:
        roles = _validate_roles(payload.roles)
        await _guard_last_administrator(db, user, roles, principal.user_id)
        for assignment in list(user.roles):
            await db.delete(assignment)
        await db.flush()
        for role in roles:
            db.add(UserRole(user_id=user.id, role=role))
    if payload.is_active is not None:
        if not payload.is_active:
            await _guard_last_administrator(db, user, set(), principal.user_id)
        user.is_active = payload.is_active
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    if payload.locale is not None:
        user.locale = payload.locale

    await db.flush()
    await db.refresh(user, ["roles"])

    audit.record(
        action="user.update",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
        before=before,
        after={
            "display_name": user.display_name,
            "roles": sorted(user.role_names),
            "is_active": user.is_active,
        },
    )
    return _out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    db: DbSession,
    auth: AuthDep,
    audit: AuditDep,
    principal: Annotated[
        PrincipalDep, Depends(require_permission(Permission.USER_MANAGE))
    ],
) -> None:
    user = await db.get(WebUser, user_id)
    if user is None:
        raise NotFoundError.of("web_user", user_id)
    if user.id == principal.user_id:
        raise ConflictError(
            params={"user": user.username},
            details="an administrator cannot delete their own account",
        )
    await _guard_last_administrator(db, user, set(), principal.user_id)

    audit.record(
        action="user.delete",
        object_type="web_user",
        object_id=user.id,
        object_label=user.username,
        before={"username": user.username, "roles": sorted(user.role_names)},
    )
    await auth.revoke_all_sessions(user.id)
    await db.delete(user)


async def _guard_last_administrator(
    db: DbSession, user: WebUser, new_roles: set[str], actor_id: str
) -> None:
    """Refuse the change that would lock everyone out.

    Losing the last administrator on an appliance that manages the monitoring
    backbone means editing the database by hand to get back in.
    """
    if RoleName.ADMINISTRATOR.value not in user.role_names:
        return
    if RoleName.ADMINISTRATOR.value in new_roles:
        return
    remaining = await db.scalars(
        select(UserRole).where(UserRole.role == RoleName.ADMINISTRATOR.value)
    )
    other_admins = [
        assignment for assignment in remaining if assignment.user_id != user.id
    ]
    if not other_admins:
        raise ConflictError(
            params={"user": user.username},
            details="this is the last administrator account",
        )
