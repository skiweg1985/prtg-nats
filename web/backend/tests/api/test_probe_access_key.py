"""Revealing the PRTG access key of an enrolled probe.

The value has to reach the operator - PRTG only accepts a probe whose key is
on the core's list - but it is a secret everywhere else: masked in job logs,
absent from the probe detail, and behind an endpoint that records who looked.
These tests pin down both halves of that bargain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from tests.conftest import write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"
ACCESS_KEY = "Berlin-01-2f1c9d3b"


async def sign_in(client: AsyncClient, role: RoleName = RoleName.ADMINISTRATOR) -> None:
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


async def probe_id_of(client: AsyncClient) -> str:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    return str(listing.json()[0]["id"])


async def test_the_key_is_revealed_with_the_account_it_belongs_to(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_probe_inventory(project_dir, PROBE, access_key=ACCESS_KEY)
    await sign_in(client)
    probe_id = await probe_id_of(client)

    revealed = await client.get(f"/api/v1/probes/{probe_id}/access-key")
    assert revealed.status_code == 200, revealed.text
    assert revealed.json() == {"nats_username": PROBE, "access_key": ACCESS_KEY}


async def test_the_probe_detail_reports_presence_but_not_the_value(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The one place the interface polls constantly must not carry the secret."""
    write_probe_inventory(project_dir, PROBE, access_key=ACCESS_KEY)
    await sign_in(client)
    probe_id = await probe_id_of(client)

    detail = await client.get(f"/api/v1/probes/{probe_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["inventory"]["access_key_present"] is True
    assert ACCESS_KEY not in detail.text


async def test_every_reveal_leaves_an_audit_record(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_probe_inventory(project_dir, PROBE, access_key=ACCESS_KEY)
    await sign_in(client)
    probe_id = await probe_id_of(client)

    await client.get(f"/api/v1/probes/{probe_id}/access-key")

    events = await client.get(
        "/api/v1/audit-events",
        params={"action": "credential.reveal", "object_id": probe_id},
    )
    assert events.status_code == 200, events.text
    recorded = events.json()
    assert len(recorded) == 1
    assert recorded[0]["object_label"] == PROBE
    # The record says that somebody looked, never at what.
    assert ACCESS_KEY not in events.text


async def test_a_probe_without_a_key_answers_not_found(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """An inventory written before the key existed, or a half-finished enrolment."""
    write_probe_inventory(project_dir, PROBE, access_key="")
    await sign_in(client)
    probe_id = await probe_id_of(client)

    revealed = await client.get(f"/api/v1/probes/{probe_id}/access-key")
    assert revealed.status_code == 404, revealed.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [(RoleName.VIEWER, 200), (RoleName.OPERATOR, 200), (RoleName.ADMINISTRATOR, 200)],
)
async def test_anyone_who_may_read_credentials_may_read_the_key(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    role: RoleName,
    expected: int,
) -> None:
    """Unlike a NATS password, which takes credential.rotate.

    The access key admits nothing on its own: PRTG pairs it with the probe's
    NATS account, and that password stays behind the stricter permission.
    """
    write_probe_inventory(project_dir, PROBE, access_key=ACCESS_KEY)
    await sign_in(client, role)
    probe_id = await probe_id_of(client)

    revealed = await client.get(f"/api/v1/probes/{probe_id}/access-key")
    assert revealed.status_code == expected, revealed.text
