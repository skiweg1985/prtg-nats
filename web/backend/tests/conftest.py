"""Shared fixtures.

Everything the tests need is built here from fakes: a temporary project
directory laid out like a real ``runtime/``, an in-file SQLite database, and a
probe-helper transport that answers from a script instead of over SSH.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.infrastructure.probe_helper import HelperRequest, ProbeConnection
from app.persistence import session as session_module
from app.persistence.base import Base


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A directory shaped like a set-up PRTG-NATS installation."""
    runtime = tmp_path / "runtime"
    for name in (
        "certs",
        "private/ssh",
        "credentials",
        "auth-users",
        "probes",
        "iperf",
        "sensor-profiles",
    ):
        (runtime / name).mkdir(parents=True, exist_ok=True)

    (tmp_path / ".env").write_text(
        "NATS_FQDN=nats.example.test\n"
        "NATS_PORT=23561\n"
        "NATS_HOST_IP=192.0.2.10\n"
        "CA_HTTP_PORT=80\n"
        "CA_ORGANIZATION=Example Org\n"
        "PRTG_CORE_IP=192.0.2.20\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def settings(project_dir: Path) -> Iterator[Settings]:
    overrides = {
        "PRTG_NATS_WEB_PROJECT_DIR": str(project_dir),
        "PRTG_NATS_WEB_ENVIRONMENT": "test",
        "PRTG_NATS_WEB_DATABASE_URL": f"sqlite+aiosqlite:///{project_dir / 'test.db'}",
        "PRTG_NATS_WEB_SESSION_COOKIE_SECURE": "false",
        "PRTG_NATS_WEB_JOB_WORKER_COUNT": "1",
        # The worker and the sync loop are started explicitly by the tests that
        # want them; a background task under every test makes failures hard to
        # attribute.
        "PRTG_NATS_WEB_INVENTORY_SYNC_INTERVAL_SECONDS": "3600",
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def session_factory(
    settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = session_module.init_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield session_module.get_session_factory()
    await session_module.dispose_engine()


@pytest_asyncio.fixture
async def db(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture
async def client(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    """An API client without the background workers.

    create_app() is not used: its lifespan starts the job runner and the
    inventory sync, and an API test should not depend on either.
    """
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from app.api.v1.router import api_router
    from app.core.errors import (
        AppError,
        app_error_handler,
        unhandled_error_handler,
        validation_error_handler,
    )

    app = FastAPI()
    app.include_router(api_router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


class ScriptedTransport:
    """A probe-helper transport driven by a lookup table.

    Keyed by command name. A value that is an Exception is raised, anything else
    is returned as the probe's answer. Every request is recorded so a test can
    assert on the exchange, not only on the outcome.
    """

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses: dict[str, object] = responses or {}
        self.calls: list[tuple[str, HelperRequest]] = []

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        self.calls.append((connection.nats_username, request))
        answer = self.responses.get(
            request.command.value, f"OK {request.command.value}\n"
        )
        if isinstance(answer, Exception):
            raise answer
        return str(answer)

    def commands(self) -> list[str]:
        return [request.command.value for _, request in self.calls]


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()


def write_probe_inventory(
    project_dir: Path,
    username: str,
    *,
    host: str = "probe.example.test",
    probe_id: str = "11111111-2222-3333-4444-555555555555",
    access_key: str = "ACCESSKEY123",
    probe_name: str = "Example Probe",
    sensors: tuple[str, ...] = (),
) -> None:
    probes = project_dir / "runtime" / "probes"
    (probes / f"{username}.env").write_text(
        f"NATS_USERNAME={username}\n"
        f"SSH_HOST={host}\n"
        "SSH_PORT=22\n"
        "PENDING_TRANSACTION=\n"
        f"PROBE_ID={probe_id}\n"
        f"ACCESS_KEY={access_key}\n"
        f"PROBE_NAME={probe_name}\n",
        encoding="utf-8",
    )
    if sensors:
        (probes / f"{username}.sensors").write_text(
            "\n".join(sensors) + "\n", encoding="utf-8"
        )
    credentials = project_dir / "runtime" / "credentials"
    (credentials / f"{username}.env").write_text(
        "NATS_FQDN=nats.example.test\n"
        "NATS_PORT=23561\n"
        f"NATS_USERNAME={username}\n"
        "NATS_PASSWORD=0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )


def write_sensor(
    project_dir: Path,
    name: str,
    *,
    version: str = "1",
    description: str = "Example sensor",
    script: str = "#!/usr/bin/env python3\nprint('{}')\n",
) -> None:
    directory = project_dir / "sensors" / name / "script"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(script, encoding="utf-8")
    (project_dir / "sensors" / name / "manifest.env").write_text(
        f"SENSOR_NAME={name}\n"
        f"SENSOR_VERSION={version}\n"
        f"SENSOR_DESCRIPTION={description}\n"
        f"SENSOR_SCRIPT=script/{name}.py\n"
        "SENSOR_PRIVILEGED=\n"
        "SENSOR_REQUIREMENTS=\n"
        "SENSOR_NEEDS_INTERFACE=false\n",
        encoding="utf-8",
    )
