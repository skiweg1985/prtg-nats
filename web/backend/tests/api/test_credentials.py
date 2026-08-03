"""NATS account management over the API.

These routes were fully implemented but never mounted, so nothing exercised
them. The enrolment wizard creates an account through POST /credentials before
it hands a probe anything, which makes this the first caller that depends on
them working - hence a test for the route, not only for the runtime writer
underneath it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName

PASSWORD = "correct-horse-battery"


async def _sign_in(
    client: AsyncClient, role: RoleName = RoleName.ADMINISTRATOR
) -> None:
    first = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert first.status_code == 201, first.text
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


async def test_an_account_can_be_created_and_listed(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await _sign_in(client)

    created = await client.post("/api/v1/credentials", json={"username": "mpp-berlin"})
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "mpp-berlin"
    assert created.json()["has_auth_entry"] is True

    listed = await client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert "mpp-berlin" in {account["username"] for account in listed.json()}


async def test_creating_an_account_writes_the_files_the_server_reads(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The password never comes back in the response - only through reveal."""
    await _sign_in(client)
    await client.post("/api/v1/credentials", json={"username": "mpp-berlin"})

    credential = project_dir / "runtime" / "credentials" / "mpp-berlin.env"
    auth_entry = project_dir / "runtime" / "auth-users" / "mpp-berlin.auth"
    assert credential.is_file() and auth_entry.is_file()
    assert credential.stat().st_mode & 0o777 == 0o600
    assert auth_entry.read_text(encoding="utf-8").startswith("mpp-berlin\t")

    revealed = await client.get("/api/v1/credentials/mpp-berlin/reveal")
    assert revealed.status_code == 200, revealed.text
    password = revealed.json()["password"]
    assert f"NATS_PASSWORD={password}\n" in credential.read_text(encoding="utf-8")
    # The hash in the server's auth file is bcrypt, never the cleartext.
    assert password not in auth_entry.read_text(encoding="utf-8")


async def test_an_invalid_account_name_is_refused(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _sign_in(client)
    response = await client.post("/api/v1/credentials", json={"username": "no spaces"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("role", "expected"),
    [(RoleName.VIEWER, 403), (RoleName.OPERATOR, 403), (RoleName.ADMINISTRATOR, 201)],
)
async def test_only_an_administrator_may_create_an_account(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    role: RoleName,
    expected: int,
) -> None:
    """Operators run the fleet; handing out NATS credentials is administration."""
    await _sign_in(client, role)
    response = await client.post("/api/v1/credentials", json={"username": "mpp-berlin"})
    assert response.status_code == expected, response.text
