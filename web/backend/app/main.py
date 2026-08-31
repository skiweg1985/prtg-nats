"""Application entry point."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas.common import (
    HealthResponse,
    ReadinessCheck,
    ReadinessResponse,
)
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger, set_correlation_id
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.probe_helper import ProbeHelperClient, SshHelperTransport
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.schema import ensure_schema
from app.persistence.session import dispose_engine, init_engine
from app.services.events import get_broadcaster
from app.services.overlay import OverlayService
from app.services.provisioning import ProvisioningService
from app.workers.inventory_sync import InventorySync
from app.workers.job_runner import JobRunner
from app.workers.stack_recovery import settle_interrupted_update
from app.workers.update_check import UpdateCheck

# Imported for the side effect of registering every model with the metadata.
# Bound to a private name rather than imported as `app.persistence.models`,
# which would bind `app` to the package and then be rebound to the FastAPI
# instance at the bottom of this file.
from app.persistence import models as _models  # noqa: F401  # isort: skip

VERSION = "0.1.0"
CORRELATION_HEADER = "X-Correlation-ID"

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(
        debug=settings.debug, json_output=settings.environment == "production"
    )

    engine = init_engine(settings)
    # Before anything else touches the database, and deliberately not guarded
    # by the environment: this used to create the missing tables and stop
    # there, which let a migration that adds a column reach an operator as a
    # service answering 500 to every request that read it.
    await ensure_schema(engine)

    runtime = RuntimeFileStore(settings)
    catalog = SensorCatalog(settings.sensor_source_dir)
    nats = NatsMonitoringClient(settings.nats_monitoring_url)
    docker = DockerAdapter(settings.docker_socket)

    # An installation from before the proxy used this CA has a complete
    # runtime/ and no interface certificate, which stops the proxy from
    # starting - and the interface that would fix it sits behind that proxy.
    # Issuing it here makes the upgrade a non-event. Restarting the proxy is
    # left to compose: it is already in a restart loop waiting for the file.
    try:
        if await asyncio.to_thread(
            ProvisioningService(settings, docker).ensure_web_certificate
        ):
            logger.info("issued the missing interface certificate")
    except Exception:
        logger.exception("could not issue the interface certificate")
    helper = ProbeHelperClient(
        SshHelperTransport(
            key_path=settings.ssh_key_path,
            known_hosts_path=settings.ssh_known_hosts_path,
            connect_timeout=settings.ssh_connect_timeout_seconds,
        ),
        default_timeout=settings.ssh_command_timeout_seconds,
    )
    broadcaster = get_broadcaster()

    runner = JobRunner(
        settings=settings,
        broadcaster=broadcaster,
        runtime=runtime,
        helper=helper,
        catalog=catalog,
        docker=docker,
    )
    sync = InventorySync(
        settings=settings,
        runtime=runtime,
        helper=helper,
        catalog=catalog,
        nats=nats,
        docker=docker,
    )

    updates = UpdateCheck(settings=settings, docker=docker)

    app.state.job_runner = runner
    app.state.inventory_sync = sync
    app.state.update_check = updates

    # Before the runner, and that order is load-bearing. An update replaces
    # this container while its job is still running, so on the way back up
    # there is a job in the database nobody is carrying. The runner's own
    # recovery would see it and mark it failed - it is written for exactly the
    # opposite case, a process that died - so the outcome has to be recorded
    # before the runner ever looks. See workers/stack_recovery.py.
    await settle_interrupted_update(settings, docker, broadcaster)

    # The overlay hub is created through the Docker socket rather than by
    # compose, so nothing else brings it back after a host reboot or a stack
    # update that collected it. Never fatal: an installation without an
    # overlay has nothing to reconcile, and one whose hub will not start still
    # has an interface to say so from.
    try:
        await OverlayService(settings, helper, docker).reconcile_hub()
    except Exception:
        logger.exception("could not reconcile the overlay hub")

    await runner.start()
    await sync.start()
    await updates.start()
    logger.info(
        "application started",
        extra={
            "environment": settings.environment,
            "project_dir": str(settings.project_dir),
            "docker": docker.available,
        },
    )

    try:
        yield
    finally:
        await updates.stop()
        await sync.stop()
        await runner.stop()
        await dispose_engine()
        logger.info("application stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} management API",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        """One id per request, echoed to the caller.

        It reaches the log lines, the error envelope, the job it starts and the
        audit record - so one identifier follows an action all the way down.
        """
        correlation_id = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex
        set_correlation_id(correlation_id)
        try:
            response: Response = await call_next(request)
        finally:
            set_correlation_id(None)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(api_router)

    @app.get("/health", response_model=HealthResponse, tags=["observability"])
    async def health() -> HealthResponse:
        """Liveness: the process is up. Never touches a dependency."""
        return HealthResponse(status="ok", version=VERSION)

    @app.get("/ready", response_model=ReadinessResponse, tags=["observability"])
    async def ready() -> ReadinessResponse:
        """Readiness: can this instance actually serve requests?

        Reports each dependency separately. A missing Docker socket is not a
        failure - the platform runs without it, with fewer actions.
        """
        runtime = RuntimeFileStore(settings)
        health_state = runtime.health()
        checks = [
            ReadinessCheck(
                name="runtime",
                ok=health_state.state != "missing",
                detail=f"state={health_state.state}",
            ),
            ReadinessCheck(
                name="database",
                ok=True,
                detail=settings.effective_database_url.split("///")[-1],
            ),
            ReadinessCheck(
                name="docker",
                ok=True,
                detail="available"
                if DockerAdapter(settings.docker_socket).available
                else "not mounted; server lifecycle actions disabled",
            ),
        ]
        return ReadinessResponse(ready=all(check.ok for check in checks), checks=checks)

    return app


app = create_app()
