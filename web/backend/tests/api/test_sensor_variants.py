"""Configuring a sensor from the interface.

The bargain these pin down: a credential typed into the form reaches
``runtime/sensor-profiles/`` and nowhere else. It is not in the answer, not in
the audit trail, and not in the job that deploys it - the job names the variant
and reads the values back out of the runtime directory itself.

The second half is the one an operator notices: an empty password field means
"leave it as it is", because the form never received the stored one and so
cannot send it back.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.persistence.models.audit import AuditEvent
from app.persistence.models.jobs import Job
from tests.conftest import REPO_ROOT, write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"
SECRET = "the-radius-password"


@pytest.fixture(autouse=True)
def real_sensors(project_dir: Path) -> None:
    """The sensors as they ship, declarations included.

    Copied rather than hand-written for the reason the rest of the fixtures
    are: a stand-in would hide exactly what these tests are about - whether
    the parameters.json that ships with wlan-auth actually produces the form
    and the profile it is supposed to.
    """
    for name in ("wlan-auth", "aruba-uplink"):
        shutil.copytree(
            REPO_ROOT / "sensors" / name,
            project_dir / "sensors" / name,
            dirs_exist_ok=True,
        )


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


async def write_variant(
    client: AsyncClient, name: str = "standort-nord", **overrides: object
) -> None:
    body: dict[str, object] = {
        "values": {"SSID": "Corporate", "AUTH": "peap", "PASSWORD": SECRET},
        "probes": [],
    }
    body.update(overrides)
    written = await client.put(f"/api/v1/sensors/wlan-auth/profiles/{name}", json=body)
    assert written.status_code == 202, written.text


async def test_a_variant_lands_in_the_runtime_directory(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await sign_in(client)
    await write_variant(client)

    stored = (
        project_dir / "runtime" / "sensor-profiles" / "wlan-auth" / "standort-nord.env"
    )
    assert stored.is_file()
    assert f"PASSWORD={SECRET}" in stored.read_text(encoding="utf-8")


async def test_the_credential_is_never_in_the_answer(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await sign_in(client)
    await write_variant(client)

    detail = await client.get("/api/v1/sensors/wlan-auth/profiles/standort-nord")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert SECRET not in detail.text
    # The settings do come back - a form that cannot show the SSID is not an
    # edit form. What the credential contributes is its name.
    assert payload["values"] == {"SSID": "Corporate", "AUTH": "peap"}
    assert payload["secrets_set"] == ["PASSWORD"]


async def test_the_credential_is_never_in_the_job_or_the_audit_trail(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    listing = await client.get("/api/v1/probes")
    probe_id = str(listing.json()[0]["id"])
    await write_variant(client, probes=[probe_id])

    async with session_factory() as session:
        jobs = (await session.scalars(select(Job))).all()
        events = (await session.scalars(select(AuditEvent))).all()

    assert jobs, "writing a variant has to produce a job"
    for job in jobs:
        assert SECRET not in json.dumps(job.payload)
        # It names the variant, so the handler can read it back itself.
        assert job.payload["profile"] == "standort-nord"
        assert job.payload["probes"] == [PROBE]
    assert events, "writing a variant has to be recorded"
    for event in events:
        assert SECRET not in json.dumps(
            {"before": event.before_state, "after": event.after_state}
        )
    written = next(event for event in events if event.action == "sensor.profile.write")
    # Field names are recorded, so an audit reader can see what was touched.
    assert written.after_state is not None
    assert "PASSWORD" in written.after_state["fields"]


async def test_an_empty_credential_keeps_the_stored_one(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The form never received the secret, so it cannot send it back.

    Without this rule every edit of a variant would silently wipe its password,
    and the sensor would start reporting a failed sign-in instead.
    """
    await sign_in(client)
    await write_variant(client)
    await write_variant(
        client, values={"SSID": "Corporate-New", "AUTH": "peap", "PASSWORD": ""}
    )

    stored = (
        project_dir / "runtime" / "sensor-profiles" / "wlan-auth" / "standort-nord.env"
    ).read_text(encoding="utf-8")
    assert f"PASSWORD={SECRET}" in stored
    assert "SSID=Corporate-New" in stored


async def test_a_missing_required_setting_is_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await sign_in(client)
    refused = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/nord",
        json={"values": {"AUTH": "peap"}, "probes": []},
    )
    assert refused.status_code == 422, refused.text
    assert "SSID" in refused.text


async def test_an_uploaded_certificate_becomes_a_path_in_the_profile(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """This is the whole point of the file kind.

    The sensor scripts take a certificate as a path and check that it exists;
    the platform's job is to put the file there and write that path where the
    script looks for it.
    """
    await sign_in(client)
    uploaded = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/standort-nord/files/CA_CERT",
        json={
            "content_base64": base64.b64encode(b"-----BEGIN CERTIFICATE-----").decode()
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["probe_path"] == (
        "/etc/prtg-nats/sensors/wlan-auth/files/standort-nord/CA_CERT.pem"
    )

    await write_variant(client)
    stored = (
        project_dir / "runtime" / "sensor-profiles" / "wlan-auth" / "standort-nord.env"
    ).read_text(encoding="utf-8")
    assert (
        "CA_CERT=/etc/prtg-nats/sensors/wlan-auth/files/standort-nord/CA_CERT.pem"
        in stored
    )


async def test_a_file_the_sensor_does_not_declare_is_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await sign_in(client)
    refused = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/nord/files/SOMETHING_ELSE",
        json={"content_base64": base64.b64encode(b"x").decode()},
    )
    assert refused.status_code == 422, refused.text


async def test_a_sensor_that_takes_everything_from_prtg_has_no_variants(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """aruba-uplink gets host and credentials as PRTG placeholders on purpose.

    Offering a variant form for it would invite someone to store on the server
    what the device settings already hold.
    """
    await sign_in(client)
    listed = await client.get("/api/v1/sensors/aruba-uplink/profiles")
    assert listed.status_code == 422, listed.text


@pytest.mark.parametrize("role", [RoleName.OPERATOR, RoleName.VIEWER])
async def test_only_a_role_with_sensor_configure_may_write_one(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    role: RoleName,
) -> None:
    from app.core.permissions import ROLE_PERMISSIONS, Permission

    await sign_in(client, role)
    written = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/nord",
        json={
            "values": {"SSID": "Corporate", "AUTH": "psk", "PSK": "wpa2-pass"},
            "probes": [],
        },
    )
    allowed = Permission.SENSOR_CONFIGURE in ROLE_PERMISSIONS[role]
    assert (written.status_code == 202) is allowed, written.text
