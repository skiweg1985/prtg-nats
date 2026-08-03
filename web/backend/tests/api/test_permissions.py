"""Authorisation is enforced on the server, on every route.

The first three tests here are structural. One fails if a router is never
mounted, one fails if a mounted route lacks a permission dependency, and one
fails if the introspection those two rely on stops seeing the routing table.
Together they survive a new endpoint written in a hurry - in either direction.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import routes as routes_package
from app.api.v1.router import api_router
from app.core.permissions import ROLE_PERMISSIONS, Permission, RoleName
from app.main import create_app
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
    # A host being enrolled has no identity yet. The invitation token is its
    # whole authorisation, which is why it is single-use, expiring and
    # revocable - and why these three routes hand out nothing that is not
    # already public or already implied by holding the token.
    (
        "GET",
        "/api/v1/enroll/{token}/bootstrap.sh",
    ): "the host cannot authenticate before it is enrolled",
    (
        "GET",
        "/api/v1/enroll/{token}/asset/{name}",
    ): "the scripts the bootstrap runs, by fixed name",
    (
        "POST",
        "/api/v1/enroll/{token}/callback",
    ): "where the host reports in and spends its invitation",
}


def _walk(
    router: APIRouter, prefix: str = ""
) -> Iterator[tuple[str, APIRoute, tuple[Any, ...]]]:
    """Yield (full path, route, dependencies inherited from the include).

    Has to recurse. FastAPI resolves included routers lazily since 0.141:
    api_router.routes holds opaque include markers rather than APIRoute
    objects, so looking only at the top level finds nothing at all - which is
    exactly how a guard test can pass while guarding nothing. Both shapes are
    handled here, and test_route_introspection_sees_every_route below fails
    loudly if a future version grows a third one.
    """
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route, ()
            continue
        # The lazy include marker: private, hence the guarded attribute access.
        included = getattr(route, "original_router", None)
        context = getattr(route, "include_context", None)
        if not isinstance(included, APIRouter) or context is None:
            continue
        inherited = tuple(getattr(context, "dependencies", ()) or ())
        for path, sub, deps in _walk(included, prefix + getattr(context, "prefix", "")):
            yield path, sub, inherited + deps


def _iter_routes() -> list[tuple[str, APIRoute, tuple[Any, ...]]]:
    return list(_walk(api_router))


def _openapi_keys() -> set[tuple[str, str]]:
    """Every versioned method/path pair a request can reach, per FastAPI itself.

    Scoped to the API prefix: /health and /ready hang off the application
    directly and never pass through api_router.
    """
    paths = create_app().openapi().get("paths", {})
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(api_router.prefix)
        for method in operations
    }


def test_route_introspection_sees_every_route() -> None:
    """Guards the guards.

    Both structural tests below walk FastAPI internals. If those internals
    change again, the walk quietly returns less and the tests keep passing
    while checking nothing. Comparing against the OpenAPI document - a public
    API built from the same routing table - turns that silence into a failure.
    """
    walked = {
        (method, path)
        for path, route, _ in _iter_routes()
        for method in route.methods or set()
    }
    assert walked >= _openapi_keys(), (
        "route introspection no longer finds every route; _walk() needs to "
        f"learn the current FastAPI routing shape. Missing: "
        f"{sorted(_openapi_keys() - walked)}"
    )


def test_every_route_module_is_mounted() -> None:
    """A router that is never included is invisible to every other check here.

    Not hypothetical: routes/credentials.py was fully implemented and called by
    the interface while api_router never included it. Every route module in
    this package is mounted without an extra prefix, so the reachable path of
    one of its routes is the API prefix plus the route's own path.
    """
    reachable = _openapi_keys()
    missing: list[str] = []
    for module_info in pkgutil.iter_modules(routes_package.__path__):
        module = importlib.import_module(
            f"{routes_package.__name__}.{module_info.name}"
        )
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            continue
        missing.extend(
            f"{module_info.name}: {method} {api_router.prefix + route.path}"
            for route in router.routes
            if isinstance(route, APIRoute)
            for method in sorted(route.methods or set())
            if (method, api_router.prefix + route.path) not in reachable
        )

    assert not missing, (
        "these routes exist but no request can reach them; include the router "
        f"in app/api/v1/router.py: {sorted(missing)}"
    )


def _guards_a_permission(route: APIRoute, inherited: tuple[Any, ...] = ()) -> bool:
    """A route is guarded when require_permission() appears in its dependants."""

    def names(dependency: Any) -> str:
        # Dependant objects expose .call; a bare Depends exposes .dependency.
        call = getattr(dependency, "call", None) or getattr(
            dependency, "dependency", None
        )
        return getattr(call, "__qualname__", "")

    if any(
        names(dependency).startswith("require_permission") for dependency in inherited
    ):
        return True
    return any(
        names(dependency).startswith("require_permission")
        for dependency in route.dependant.dependencies
    ) or any(
        names(sub).startswith("require_permission")
        for dependency in route.dependant.dependencies
        for sub in dependency.dependencies
    )


def test_every_route_is_guarded_or_listed_as_an_exception() -> None:
    unguarded: list[str] = []
    for path, route, inherited in _iter_routes():
        for method in sorted(route.methods or set()):
            if (method, path) in UNGUARDED:
                continue
            if not _guards_a_permission(route, inherited):
                unguarded.append(f"{method} {path}")

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
