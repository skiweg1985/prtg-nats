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
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.persistence.models.audit import AuditEvent
from app.persistence.models.jobs import Job
from tests.conftest import REPO_ROOT, SETUP_CODE, write_probe_inventory

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
        "/api/v1/auth/setup",
        json={"setup_code": SETUP_CODE, "username": "admin", "password": PASSWORD},
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
        "values": {
            "SSID": "Corporate",
            "AUTH": "peap",
            "IDENTITY": "svc-wlan-monitor",
            "PASSWORD": SECRET,
        },
        "probes": [],
    }
    body.update(overrides)
    written = await client.put(f"/api/v1/sensors/wlan-auth/profiles/{name}", json=body)
    assert written.status_code == 202, written.text


def _pair(encrypt: str | None = None) -> tuple[bytes, bytes]:
    """A certificate and the key it was made for, both as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "probe")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    protection: serialization.KeySerializationEncryption = serialization.NoEncryption()
    if encrypt is not None:
        protection = serialization.BestAvailableEncryption(encrypt.encode("utf-8"))
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            protection,
        ),
    )


def _revocation_list() -> bytes:
    """What a Windows certificate names beside its issuer, and gets mistaken
    for it often enough that the refusal has to say what it is."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example CA")])
    now = dt.datetime.now(dt.UTC)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(name)
        .last_update(now)
        .next_update(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return crl.public_bytes(serialization.Encoding.PEM)


async def upload(
    client: AsyncClient, key: str, content: bytes, profile: str = "standort-nord"
):
    return await client.put(
        f"/api/v1/sensors/wlan-auth/profiles/{profile}/files/{key}",
        json={"content_base64": base64.b64encode(content).decode()},
    )


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
    assert payload["values"] == {
        "SSID": "Corporate",
        "AUTH": "peap",
        "IDENTITY": "svc-wlan-monitor",
    }
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
        client,
        values={
            "SSID": "Corporate-New",
            "AUTH": "peap",
            "IDENTITY": "svc-wlan-monitor",
            "PASSWORD": "",
        },
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
        json={"content_base64": base64.b64encode(_pair()[0]).decode()},
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


async def test_a_certificate_slot_refuses_what_is_not_a_certificate(
    client: AsyncClient,
) -> None:
    """The revocation list instead of the issuing CA.

    A Windows certificate names its CRL in the same extension block as its
    issuer, so the wrong link is easy to follow. Taken without a word, the
    probe answered minutes later with "RADIUS certificate rejected" - a
    verdict on the server for a file on this side.
    """
    await sign_in(client)
    refused = await upload(client, "CA_CERT", _revocation_list())
    assert refused.status_code == 422, refused.text
    assert "revocation list" in refused.json()["error"]["details"]

    certificate, _ = _pair()
    assert (await upload(client, "CA_CERT", certificate)).status_code == 200


async def test_a_certificate_is_stored_as_pem_whatever_it_arrived_as(
    client: AsyncClient, project_dir: Path
) -> None:
    """A Windows CA hands out DER, and wpa_supplicant reads PEM."""
    await sign_in(client)
    certificate, _ = _pair()
    der = x509.load_pem_x509_certificate(certificate).public_bytes(
        serialization.Encoding.DER
    )
    assert (await upload(client, "CA_CERT", der)).status_code == 200

    stored = (
        project_dir
        / "runtime"
        / "sensor-profiles"
        / "wlan-auth"
        / "files"
        / "standort-nord"
        / "CA_CERT.pem"
    ).read_bytes()
    assert stored.startswith(b"-----BEGIN CERTIFICATE-----")


async def test_a_key_from_another_pair_is_refused(client: AsyncClient) -> None:
    """The fault that reads like a rejected sign-in.

    wpa_supplicant cannot build its TLS context, the EAP exchange ends before
    anything reaches the RADIUS server, and the sensor used to report
    "credentials rejected by RADIUS" for it.
    """
    await sign_in(client)
    certificate, _ = _pair()
    _, stranger = _pair()

    assert (await upload(client, "CLIENT_CERT", certificate)).status_code == 200
    refused = await upload(client, "PRIVATE_KEY", stranger)
    assert refused.status_code == 422, refused.text
    assert "different pairs" in refused.json()["error"]["details"]


async def test_the_pair_is_checked_whichever_half_arrives_second(
    client: AsyncClient,
) -> None:
    await sign_in(client)
    certificate, key = _pair()
    _, stranger = _pair()

    assert (await upload(client, "PRIVATE_KEY", stranger)).status_code == 200
    refused = await upload(client, "CLIENT_CERT", certificate)
    assert refused.status_code == 422, refused.text

    assert (await upload(client, "PRIVATE_KEY", key)).status_code == 200
    assert (await upload(client, "CLIENT_CERT", certificate)).status_code == 200


async def test_an_encrypted_key_is_judged_by_the_passphrase_of_the_variant(
    client: AsyncClient,
) -> None:
    """The passphrase arrives after the files, so the pair is judged twice.

    The interface writes the uploads first and the values after them, so at
    upload time there is often nothing to open the key with. Saying nothing
    then and refusing at the write is what turns a typo in a password field
    into a message, instead of a probe reporting a rejected sign-in a scan
    later.
    """
    await sign_in(client)
    certificate, key = _pair(encrypt="geheim")

    assert (await upload(client, "CLIENT_CERT", certificate)).status_code == 200
    assert (await upload(client, "PRIVATE_KEY", key)).status_code == 200

    body = {
        "values": {
            "SSID": "Corporate",
            "AUTH": "eap-tls",
            "IDENTITY": "probe",
            "PRIVATE_KEY_PASSWD": "falsch",
            "NO_VERIFY_SERVER": "true",
        },
        "probes": [],
    }
    refused = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/standort-nord", json=body
    )
    assert refused.status_code == 422, refused.text
    assert "does not open it" in refused.json()["error"]["details"]

    body["values"]["PRIVATE_KEY_PASSWD"] = "geheim"  # type: ignore[index]
    written = await client.put(
        "/api/v1/sensors/wlan-auth/profiles/standort-nord", json=body
    )
    assert written.status_code == 202, written.text
