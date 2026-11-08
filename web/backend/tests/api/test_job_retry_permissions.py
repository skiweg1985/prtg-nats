"""A retry needs the permission of the job it repeats, not only job.retry.

job.retry is an operator permission; many job types are not. Without the
second check, any finished job an administrator ran - a certificate renewal,
a stack update - would be one click away for every operator.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import ROLE_PERMISSIONS, Permission, RoleName
from app.domain.enums import JobStatus
from app.persistence.models.jobs import Job
from app.services.events import get_broadcaster
from app.services.jobs import JobRequest, JobService
from app.workers.handlers import REGISTRY
from app.workers.handlers.iperf_provisioning import FOREIGN_CREDENTIALS_JOB_TYPE

PASSWORD = "correct-horse-battery"

OPERATOR = ROLE_PERMISSIONS[RoleName.OPERATOR]

# Every job type an operator must not be able to start, retry included.
ADMIN_ONLY = sorted(
    job_type
    for job_type, definition in REGISTRY.items()
    if Permission(definition.permission) not in OPERATOR
)

OPERATOR_ALLOWED = sorted(
    job_type
    for job_type, definition in REGISTRY.items()
    if Permission(definition.permission) in OPERATOR
    # Needs the replacement password again; refused for everyone.
    and job_type != FOREIGN_CREDENTIALS_JOB_TYPE
)


async def _sign_in_as(client: AsyncClient, role: RoleName) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    if role is RoleName.ADMINISTRATOR:
        return
    created = await client.post(
        "/api/v1/users",
        json={
            "username": role.value,
            "password": PASSWORD,
            "roles": [role.value],
            "must_change_password": False,
        },
    )
    assert created.status_code == 201, created.text
    await client.post("/api/v1/auth/logout")
    signed_in = await client.post(
        "/api/v1/auth/login", json={"username": role.value, "password": PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text


async def _finished_job(
    session_factory: async_sessionmaker[AsyncSession], job_type: str
) -> str:
    async with session_factory() as db:
        jobs = JobService(db, get_broadcaster())
        job = await jobs.create(JobRequest(type=job_type, steps=("a",)))
        await jobs.finish(job, JobStatus.FAILED)
        await db.commit()
        return job.id


async def _job_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as db:
        return int(await db.scalar(select(func.count()).select_from(Job)) or 0)


def test_the_operator_role_leaves_some_job_types_to_administrators() -> None:
    """Guards the parametrisation below against silently testing nothing."""
    assert "certificate.renew" in ADMIN_ONLY
    assert "stack.update" in ADMIN_ONLY
    assert "system.restart" in ADMIN_ONLY


@pytest.mark.parametrize("job_type", ADMIN_ONLY)
async def test_an_operator_cannot_retry_a_job_they_could_not_start(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    job_type: str,
) -> None:
    await _sign_in_as(client, RoleName.ADMINISTRATOR)
    job_id = await _finished_job(session_factory, job_type)
    await _sign_in_as(client, RoleName.OPERATOR)
    before = await _job_count(session_factory)

    response = await client.post(f"/api/v1/jobs/{job_id}/retry")

    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "auth.permission_denied"
    assert error["params"]["permission"] == REGISTRY[job_type].permission
    assert await _job_count(session_factory) == before


@pytest.mark.parametrize("job_type", OPERATOR_ALLOWED)
async def test_an_operator_may_retry_a_job_they_could_start(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    job_type: str,
) -> None:
    await _sign_in_as(client, RoleName.ADMINISTRATOR)
    job_id = await _finished_job(session_factory, job_type)
    await _sign_in_as(client, RoleName.OPERATOR)

    response = await client.post(f"/api/v1/jobs/{job_id}/retry")

    assert response.status_code == 202, response.text


@pytest.mark.parametrize("job_type", ADMIN_ONLY)
async def test_an_administrator_may_still_retry_every_job_type(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    job_type: str,
) -> None:
    await _sign_in_as(client, RoleName.ADMINISTRATOR)
    job_id = await _finished_job(session_factory, job_type)

    response = await client.post(f"/api/v1/jobs/{job_id}/retry")

    assert response.status_code == 202, response.text


async def test_a_job_type_without_a_definition_is_not_retried(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A type from an older release has no permission to check against."""
    await _sign_in_as(client, RoleName.ADMINISTRATOR)
    job_id = await _finished_job(session_factory, "legacy.retired_job")
    before = await _job_count(session_factory)

    response = await client.post(f"/api/v1/jobs/{job_id}/retry")

    assert response.status_code == 409, response.text
    assert await _job_count(session_factory) == before
