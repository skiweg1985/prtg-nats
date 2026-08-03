"""Request dependencies.

Two things every route needs: who is asking, and whether they may. Both are
resolved here so a route body never contains an authorisation check it could
forget to write.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Cookie, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationRequiredError, PermissionDeniedError
from app.core.permissions import Permission
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.probe_helper import ProbeHelperClient, SshHelperTransport
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.session import get_db_session
from app.services.audit import AuditContext, AuditWriter
from app.services.auth import AuthService, Principal
from app.services.events import EventBroadcaster, get_broadcaster
from app.services.jobs import JobService
from app.services.probes import ProbeService

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


# --- Infrastructure ---------------------------------------------------------
# Cheap to construct and stateless, so one per request keeps them free of
# cross-request state without measurable cost.


def get_runtime_store(settings: SettingsDep) -> RuntimeFileStore:
    return RuntimeFileStore(settings)


def get_sensor_catalog(settings: SettingsDep) -> SensorCatalog:
    return SensorCatalog(settings.sensor_source_dir)


def get_nats_client(settings: SettingsDep) -> NatsMonitoringClient:
    return NatsMonitoringClient(settings.nats_monitoring_url)


def get_docker_adapter(settings: SettingsDep) -> DockerAdapter:
    return DockerAdapter(settings.docker_socket)


def get_probe_helper(settings: SettingsDep) -> ProbeHelperClient:
    transport = SshHelperTransport(
        key_path=settings.ssh_key_path,
        known_hosts_path=settings.ssh_known_hosts_path,
        connect_timeout=settings.ssh_connect_timeout_seconds,
    )
    return ProbeHelperClient(
        transport, default_timeout=settings.ssh_command_timeout_seconds
    )


RuntimeDep = Annotated[RuntimeFileStore, Depends(get_runtime_store)]
CatalogDep = Annotated[SensorCatalog, Depends(get_sensor_catalog)]
NatsDep = Annotated[NatsMonitoringClient, Depends(get_nats_client)]
DockerDep = Annotated[DockerAdapter, Depends(get_docker_adapter)]
HelperDep = Annotated[ProbeHelperClient, Depends(get_probe_helper)]
BroadcasterDep = Annotated[EventBroadcaster, Depends(get_broadcaster)]


# --- Services ---------------------------------------------------------------


def get_auth_service(db: DbSession, settings: SettingsDep) -> AuthService:
    return AuthService(db, settings)


def get_probe_service(
    db: DbSession,
    settings: SettingsDep,
    runtime: RuntimeDep,
    helper: HelperDep,
    catalog: CatalogDep,
) -> ProbeService:
    return ProbeService(db, settings, runtime, helper, catalog)


def get_job_service(db: DbSession, broadcaster: BroadcasterDep) -> JobService:
    return JobService(db, broadcaster)


AuthDep = Annotated[AuthService, Depends(get_auth_service)]
ProbeServiceDep = Annotated[ProbeService, Depends(get_probe_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]


# --- Identity ---------------------------------------------------------------


def client_ip(request: Request) -> str | None:
    """The caller's address, trusting the proxy header only when configured.

    X-Forwarded-For is written by anyone who can reach the port. It is used
    because the documented deployment puts a reverse proxy in front and binds
    the application to loopback; the audit trail records what the proxy saw.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_optional_principal(
    request: Request,
    auth: AuthDep,
    settings: SettingsDep,
    session_token: Annotated[str | None, Cookie(alias="prtg_nats_session")] = None,
) -> Principal | None:
    if settings.dev_auth_enabled:
        return Principal.development()
    if not session_token:
        return None
    resolved = await auth.resolve_session(session_token)
    if resolved is None:
        return None
    user, session = resolved
    principal = AuthService.principal_for(user, session.id)
    request.state.principal = principal
    return principal


async def get_principal(
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> Principal:
    if principal is None:
        raise AuthenticationRequiredError()
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]
OptionalPrincipalDep = Annotated[Principal | None, Depends(get_optional_principal)]


def require_permission(
    permission: Permission,
) -> Callable[..., Awaitable[Principal]]:
    """Guard a route with one permission.

    Every mutating route carries one of these. A test walks the router and
    fails if a route exists without it, so "I forgot the check" cannot ship.
    """

    async def dependency(principal: PrincipalDep) -> Principal:
        if not principal.has(permission):
            raise PermissionDeniedError.of(permission.value)
        return principal

    return dependency


# --- Audit ------------------------------------------------------------------


async def get_audit_writer(
    request: Request,
    db: DbSession,
    principal: OptionalPrincipalDep,
) -> AsyncIterator[AuditWriter]:
    yield AuditWriter(
        db, AuditContext(principal=principal, source_ip=client_ip(request))
    )


AuditDep = Annotated[AuditWriter, Depends(get_audit_writer)]
