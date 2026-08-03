"""Authorisation is enforced on the server, on every route.

The first test here is structural: it walks the router and fails if any route
lacks a permission dependency. That is the check that survives a new endpoint
written in a hurry.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.router import api_router
from app.core.permissions import ROLE_PERMISSIONS, Permission, RoleName
from app.workers.handlers import REGISTRY

PASSWORD = "correct-horse-battery"

# Routes that are deliberately reachable without a permission, each for a
# reason that has to stay true.
UNGUARDED: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/auth/login"): "signing in cannot require being signed in",
    ("POST", "/api/v1/auth/logout"): "ending a session needs only a session",
    ("GET", "/api/v1/auth/state"): "decides whether to show login or setup",
    (
        "POST",
        "/api/v1/auth/setup",
    ): "creates the first account; refuses once one exists",
    ("GET", "/api/v1/auth/me"): "the caller's own identity",
    ("POST", "/api/v1/auth/change-password"): "changing one's own password",
    ("GET", "/api/v1/system/capabilities"): "what the interface may render at all",
}


def _iter_routes() -> list[APIRoute]:
    return [route for route in api_router.routes if isinstance(route, APIRoute)]


def _guards_a_permission(route: APIRoute) -> bool:
    """A route is guarded when require_permission() appears in its dependants."""
    return any(
        getattr(dependency.call, "__qualname__", "").startswith("require_permission")
        for dependency in route.dependant.dependencies
    ) or any(
        getattr(sub.call, "__qualname__", "").startswith("require_permission")
        for dependency in route.dependant.dependencies
        for sub in dependency.dependencies
    )


def test_every_route_is_guarded_or_listed_as_an_exception() -> None:
    unguarded: list[str] = []
    for route in _iter_routes():
        for method in sorted(route.methods or set()):
            if (method, route.path) in UNGUARDED:
                continue
            if not _guards_a_permission(route):
                unguarded.append(f"{method} {route.path}")

    assert not unguarded, (
        "these routes have no permission dependency; add one or document the "
        f"exception in UNGUARDED: {unguarded}"
    )


def test_every_job_type_declares_the_permission_it_needs() -> None:
    """A job that can be created without a stated permission is a hole."""
    known = {permission.value for permission in Permission}
    for job_type, definition in REGISTRY.items():
        assert definition.permission in known, (
            f"job {job_type} declares unknown permission {definition.permission}"
        )
        assert definition.steps, f"job {job_type} has no steps"


def test_viewer_may_only_read() -> None:
    for permission in ROLE_PERMISSIONS[RoleName.VIEWER]:
        assert permission.value.endswith(".read")


def test_operator_cannot_touch_credentials_or_users() -> None:
    """The line between operating and administering the platform."""
    operator = ROLE_PERMISSIONS[RoleName.OPERATOR]
    for forbidden in (
        Permission.CREDENTIAL_ROTATE,
        Permission.CERTIFICATE_RENEW,
        Permission.USER_MANAGE,
        Permission.ROLE_MANAGE,
        Permission.SYSTEM_RESTART,
        Permission.SYSTEM_SETTINGS,
        Permission.PROBE_DELETE,
        Permission.PROBE_CREATE,
    ):
        assert forbidden not in operator


def test_operator_may_deploy() -> None:
    operator = ROLE_PERMISSIONS[RoleName.OPERATOR]
    assert Permission.SENSOR_DEPLOY in operator
    assert Permission.DEPLOYMENT_CREATE in operator
    assert Permission.JOB_RETRY in operator


def test_administrator_has_everything() -> None:
    assert ROLE_PERMISSIONS[RoleName.ADMINISTRATOR] == frozenset(Permission)


async def _sign_in_as(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    role: RoleName,
) -> None:
    """Create the first administrator, then a user in `role`, and sign in as them."""
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    if role is not RoleName.ADMINISTRATOR:
        response = await client.post(
            "/api/v1/users",
            json={
                "username": role.value,
                "password": PASSWORD,
                "roles": [role.value],
                "must_change_password": False,
            },
        )
        assert response.status_code == 201, response.text
        await client.post("/api/v1/auth/logout")
        signed_in = await client.post(
            "/api/v1/auth/login",
            json={"username": role.value, "password": PASSWORD},
        )
        assert signed_in.status_code == 200, signed_in.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [(RoleName.VIEWER, 403), (RoleName.OPERATOR, 403), (RoleName.ADMINISTRATOR, 202)],
)
async def test_only_an_administrator_may_renew_a_certificate(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    role: RoleName,
    expected: int,
) -> None:
    await _sign_in_as(client, session_factory, role)
    response = await client.post("/api/v1/certificates/server/renew")
    assert response.status_code == expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [(RoleName.VIEWER, 403), (RoleName.OPERATOR, 200), (RoleName.ADMINISTRATOR, 200)],
)
async def test_a_viewer_cannot_edit_a_probe(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir,
    role: RoleName,
    expected: int,
) -> None:
    from tests.conftest import write_probe_inventory

    write_probe_inventory(project_dir, "mpp-berlin-01")
    await _sign_in_as(client, session_factory, role)

    listed = await client.get("/api/v1/probes")
    assert listed.status_code == 200
    probe_id = listed.json()[0]["id"]

    response = await client.patch(
        f"/api/v1/probes/{probe_id}", json={"display_name": "Berlin"}
    )
    assert response.status_code == expected


async def test_a_denied_request_names_the_missing_permission(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """So the operator can ask for the right thing instead of "access denied"."""
    await _sign_in_as(client, session_factory, RoleName.VIEWER)
    response = await client.post("/api/v1/certificates/server/renew")

    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "auth.permission_denied"
    assert body["params"]["permission"] == "certificate.renew"


async def test_the_last_administrator_cannot_be_demoted(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Losing it means editing the database by hand to get back in."""
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    me = await client.get("/api/v1/auth/me")
    user_id = me.json()["user_id"]

    response = await client.patch(
        f"/api/v1/users/{user_id}", json={"roles": ["viewer"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "common.conflict"
