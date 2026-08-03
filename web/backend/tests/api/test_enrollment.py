"""The invitation channel.

Three routes here are reachable without a session, because a host being
enrolled has no identity yet. That makes the token the entire authorisation,
so what these tests hold is narrow and blunt: the token is single-use, it
expires, it never appears in the database in the clear, an invalid one cannot
be told apart from an expired one, and the script it hands out cannot be
talked into serving an arbitrary file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.permissions import RoleName
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.inventory import EnrollmentToken
from app.services.events import get_broadcaster
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport

PASSWORD = "correct-horse-battery"


def _build_runner(settings: Settings, transport: ScriptedTransport) -> JobRunner:
    return JobRunner(
        settings=settings,
        broadcaster=get_broadcaster(),
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        docker=DockerAdapter(settings.docker_socket),
    )


async def _drain(runner: JobRunner, *, rounds: int = 12) -> None:
    """Run the queued enrolment to completion without the polling loop."""
    for _ in range(rounds):
        if not await runner._claim_and_run():
            return


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


async def _initialise(project_dir: Path) -> None:
    """A runtime with a CA and a management key, which an invitation needs."""
    from app.core.config import get_settings
    from app.infrastructure.docker import DockerAdapter
    from app.services.provisioning import ProvisioningService

    settings = get_settings()
    ProvisioningService(settings, DockerAdapter(settings)).initialise_runtime()


async def _invite(client: AsyncClient, **overrides: object) -> dict:
    body = {"nats_username": "mpp-berlin", "probe_name": "Berlin"} | overrides
    response = await client.post("/api/v1/probes/enrollment/tokens", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_an_invitation_returns_a_command_and_the_ca_fingerprint(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await _sign_in(client)
    await _initialise(project_dir)

    issued = await _invite(client)

    assert issued["token"]
    assert issued["ca_sha256"]
    command = issued["command"]
    # The ceremony, in one line: fetch the CA over plain HTTP, check it against
    # a fingerprint that came through the browser, then speak TLS against it.
    assert "http://nats.example.test/nats-ca.pem" in command
    assert issued["ca_sha256"] in command
    assert "sha256sum -c -" in command
    assert f"/enroll/{issued['token']}/bootstrap.sh" in command
    assert "--cacert" in command
    assert "-k" not in command and "--insecure" not in command


async def test_the_one_liner_path_is_one_the_proxy_actually_forwards(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The two halves of the public URL live in different files.

    The command says https://host:8443/enroll/... ; the API serves
    /api/v1/enroll/... ; Caddy bridges the two. Nothing in either file knows
    about the other, so this compares them - the first real enrolment failed
    with a 404 for exactly this reason.
    """
    import re as _re

    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)

    caddyfile = (Path(__file__).resolve().parents[3] / "Caddyfile").read_text(
        encoding="utf-8"
    )

    # The proxy forwards /enroll/* ...
    assert _re.search(r"handle\s+/enroll/\*", caddyfile), (
        "the proxy no longer forwards the path the one-liner uses"
    )
    # ... and rewrites it onto the API prefix the routes are actually mounted at.
    assert _re.search(r"rewrite\s+\*\s+/api/v1\{uri\}", caddyfile), (
        "the proxy forwards /enroll/* without rewriting it onto /api/v1"
    )
    assert "/enroll/" in issued["command"]
    assert "/api/v1/" not in issued["command"]


@pytest.mark.parametrize(
    "probe_name",
    ["Testprobe 191", "probe/one", "-leading-hyphen", "a" * 129, ""],
)
async def test_an_unusable_probe_name_is_refused_before_anything_happens(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    probe_name: str,
) -> None:
    """The renderer refuses these names too - but far too late.

    By then the invitation is redeemed, the NATS account created and the
    inventory written, and the operator is looking at a half-finished
    enrolment because of a space in a name. Found while enrolling a real
    probe as "Testprobe 191".
    """
    await _sign_in(client)
    await _initialise(project_dir)

    response = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin", "probe_name": probe_name},
    )
    assert response.status_code == 422, response.text
    assert "probe_name" in str(response.json()["error"].get("fields"))

    # And nothing was created on the way to refusing.
    assert (await client.get("/api/v1/probes/enrollment/tokens")).json() == []
    assert (await client.get("/api/v1/credentials")).json() == [
        account
        for account in (await client.get("/api/v1/credentials")).json()
        if account["username"] != "mpp-berlin"
    ]


async def test_a_host_that_is_already_enrolled_is_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """One host, one inventory entry.

    The management access lives on the host, not in the entry. A second entry
    for the same address shares it, and retiring either one revokes it for
    both - the survivor then reads as unreachable while still connected to
    NATS. Reproduced on a real installation before this check existed.
    """
    from tests.conftest import write_probe_inventory

    await _sign_in(client)
    await _initialise(project_dir)
    write_probe_inventory(project_dir, "mpp-berlin", host="192.0.2.10")

    response = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin-two", "expected_host": "192.0.2.10"},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "probe.host_already_enrolled"
    # Names the entry in the way, so the operator can decide what to do with
    # it rather than go looking.
    assert error["params"]["probe"] == "mpp-berlin"
    assert error["params"]["host"] == "192.0.2.10"

    # Nothing was created on the way to refusing.
    assert (await client.get("/api/v1/probes/enrollment/tokens")).json() == []


async def test_re_enrolling_the_same_probe_is_still_allowed(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The normal case: a probe that was rebuilt, under the account it had."""
    from tests.conftest import write_probe_inventory

    await _sign_in(client)
    await _initialise(project_dir)
    write_probe_inventory(project_dir, "mpp-berlin", host="192.0.2.10")

    response = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin", "expected_host": "192.0.2.10"},
    )
    assert response.status_code == 201, response.text


async def test_a_different_host_is_not_confused_with_an_enrolled_one(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    from tests.conftest import write_probe_inventory

    await _sign_in(client)
    await _initialise(project_dir)
    write_probe_inventory(project_dir, "mpp-berlin", host="192.0.2.10")

    response = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-hamburg", "expected_host": "192.0.2.11"},
    )
    assert response.status_code == 201, response.text


async def test_the_check_survives_a_missing_expected_host(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Without an address there is nothing to compare - the handler checks
    again with the one the host reports from."""
    from tests.conftest import write_probe_inventory

    await _sign_in(client)
    await _initialise(project_dir)
    write_probe_inventory(project_dir, "mpp-berlin", host="192.0.2.10")

    response = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-hamburg"},
    )
    assert response.status_code == 201, response.text


async def test_the_token_is_never_stored_in_the_clear(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """A database dump must not hand out the right to enrol a host."""
    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)
    token = issued["token"]

    async with session_factory() as session:
        tables = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
        hits: list[str] = []
        for (table,) in tables.all():
            rows = await session.execute(text(f'SELECT * FROM "{table}"'))  # noqa: S608
            for row in rows.mappings().all():
                for column, value in row.items():
                    if isinstance(value, str) and token in value:
                        hits.append(f"{table}.{column}")

    assert not hits, f"the cleartext token reached the database: {hits}"


async def test_fetching_the_script_does_not_spend_the_invitation(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """A half-finished run has to be retryable without a new token."""
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    for _ in range(2):
        response = await client.get(f"/api/v1/enroll/{token}/bootstrap.sh")
        assert response.status_code == 200, response.text
        assert response.text.startswith("#!/bin/sh")


async def test_the_script_carries_the_ca_and_the_management_key(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """Both are public, and inlining them removes two ways to fail halfway."""
    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)

    script = (await client.get(f"/api/v1/enroll/{issued['token']}/bootstrap.sh")).text

    assert "-----BEGIN CERTIFICATE-----" in script
    assert "ssh-ed25519 " in script
    assert issued["ca_sha256"] in script
    # Nothing left unrendered - a stray placeholder would reach a root shell.
    assert "@@" not in script
    # The source restriction has to be concrete, or the management key would be
    # accepted from anywhere.
    assert 'SSH_SOURCE_CIDR="192.0.2.10/32"' in script


async def test_the_script_hands_the_installer_a_nats_endpoint(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The bootstrap runs from a pipe, so it cannot answer a prompt.

    install-mpp.sh has no default for the NATS host and asks for it at a
    terminal; without one it refuses before touching a package manager. The
    bootstrap used to omit it, which nobody noticed because every probe that
    ever reached that branch already carried the package. On the first host
    that did not, the installation died with "--nats-host is required without
    an interactive terminal" and the enrolment failed four steps later over a
    missing systemd unit.
    """
    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)

    script = (await client.get(f"/api/v1/enroll/{issued['token']}/bootstrap.sh")).text

    assert 'NATS_HOST="nats.example.test"' in script
    assert 'NATS_PORT="23561"' in script
    # And they reach the installer rather than only sitting in a variable.
    assert '--nats-host "${NATS_HOST}"' in script
    assert '--nats-port "${NATS_PORT}"' in script


async def test_the_callback_spends_the_invitation_exactly_once(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    report = {
        "hostname": "probe.example.test",
        "ssh_port": 22,
        "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"],
        "access_installed": True,
        "package_installed": True,
    }
    first = await client.post(f"/api/v1/enroll/{token}/callback", json=report)
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] is True

    second = await client.post(f"/api/v1/enroll/{token}/callback", json=report)
    assert second.status_code == 404
    assert second.json()["error"]["code"] == "enrollment.token_invalid"

    # And the script is gone with it.
    assert (await client.get(f"/api/v1/enroll/{token}/bootstrap.sh")).status_code == 404


async def test_what_the_host_reported_is_kept(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """The host keys above all - they are what gets pinned."""
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    await client.post(
        f"/api/v1/enroll/{token}/callback",
        json={
            "hostname": "probe.example.test",
            "ssh_port": 2222,
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"],
            "access_installed": True,
            "package_installed": False,
            "package_error": "E: Unable to locate package prtgmpprobe",
        },
    )

    async with session_factory() as session:
        record = (await session.execute(select(EnrollmentToken))).scalar_one()
        assert record.redeemed_at is not None
        assert record.reported["ssh_port"] == 2222
        assert record.reported["host_keys"] == [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"
        ]
        assert record.reported["package_installed"] is False
        # Kept with the rest of the report: what the host could not do is as
        # much a part of it as what it did.
        assert "Unable to locate package" in record.reported["package_error"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda r: setattr(r, "revoked_at", datetime.now(UTC)), id="revoked"
        ),
        pytest.param(
            lambda r: setattr(
                r, "expires_at", datetime.now(UTC) - timedelta(minutes=1)
            ),
            id="expired",
        ),
    ],
)
async def test_an_unusable_invitation_is_indistinguishable_from_an_unknown_one(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    mutate,
) -> None:
    """The caller is unauthenticated; a distinct 'expired' confirms it existed."""
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    async with session_factory() as session:
        record = (await session.execute(select(EnrollmentToken))).scalar_one()
        mutate(record)
        await session.commit()

    unusable = await client.get(f"/api/v1/enroll/{token}/bootstrap.sh")
    unknown = await client.get("/api/v1/enroll/not-a-real-token/bootstrap.sh")

    assert unusable.status_code == unknown.status_code == 404
    assert (
        unusable.json()["error"]["code"]
        == unknown.json()["error"]["code"]
        == "enrollment.token_invalid"
    )


async def test_the_bootstrap_can_fetch_what_it_runs(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """enroll-probe.sh is served, not reimplemented.

    The from=, restrict and forced-command rules that make the management
    access safe stay defined in that one file - so the platform has to be able
    to hand out exactly it.
    """
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    served = await client.get(f"/api/v1/enroll/{token}/asset/enroll-probe.sh")
    assert served.status_code == 200, served.text
    assert served.text == (project_dir / "libexec" / "enroll-probe.sh").read_text()
    assert 'command="sudo -n /usr/local/sbin/prtg-nats-probe-helper"' in served.text

    for name in ("prtg-nats-probe-helper", "install-mpp.sh"):
        response = await client.get(f"/api/v1/enroll/{token}/asset/{name}")
        assert response.status_code == 200, f"{name}: {response.text}"


@pytest.mark.parametrize(
    "name",
    ["../../../etc/passwd", "/etc/passwd", "nats-server.conf.template", "config"],
)
async def test_the_asset_route_serves_a_fixed_set_and_nothing_else(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    name: str,
) -> None:
    """A valid token is not a reason to let the caller name a file."""
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]

    response = await client.get(f"/api/v1/enroll/{token}/asset/{name}")
    assert response.status_code in (404, 405), response.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [(RoleName.VIEWER, 403), (RoleName.OPERATOR, 403), (RoleName.ADMINISTRATOR, 201)],
)
async def test_only_an_administrator_may_invite_a_probe(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    role: RoleName,
    expected: int,
) -> None:
    """probe.create is not an operator permission - enrolling is administration."""
    await _sign_in(client, role)
    await _initialise(project_dir)

    response = await client.post(
        "/api/v1/probes/enrollment/tokens", json={"nats_username": "mpp-berlin"}
    )
    assert response.status_code == expected, response.text


async def test_a_revoked_invitation_disappears_from_the_list(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)

    listed = await client.get("/api/v1/probes/enrollment/tokens")
    assert [item["id"] for item in listed.json()] == [issued["id"]]
    # The token itself is not in the listing - it existed once, in the response
    # that created it.
    assert "token" not in listed.json()[0]

    revoked = await client.delete(f"/api/v1/probes/enrollment/tokens/{issued['id']}")
    assert revoked.status_code == 204

    assert (await client.get("/api/v1/probes/enrollment/tokens")).json() == []
    # Still readable by id, which is how the wizard finds out it is over.
    read = await client.get(f"/api/v1/probes/enrollment/tokens/{issued['id']}")
    assert read.status_code == 200, read.text
    assert read.json()["revoked_at"] is not None


async def test_a_redeemed_invitation_still_names_the_job_it_started(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """What the wizard waits for, and where it used to lose it.

    The callback redeems the invitation and writes the job id in the same
    request, so the record leaves the open list at the very moment it gains
    the job. A caller following only that list waits forever while the
    enrolment it started runs to completion behind it - observed as a browser
    stuck on "waiting for the probe" with the probe long since enrolled.
    """
    await _sign_in(client)
    await _initialise(project_dir)
    issued = await _invite(client)

    callback = await client.post(
        f"/api/v1/enroll/{issued['token']}/callback",
        json={
            "hostname": "probe.example.test",
            "ssh_port": 22,
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"],
            "access_installed": True,
            "package_installed": True,
        },
    )
    assert callback.status_code == 200, callback.text
    job_id = callback.json()["job_id"]
    assert job_id

    # Out of the open list, because it is spent ...
    assert (await client.get("/api/v1/probes/enrollment/tokens")).json() == []

    # ... and still readable by id, carrying the job there is to watch.
    read = await client.get(f"/api/v1/probes/enrollment/tokens/{issued['id']}")
    assert read.status_code == 200, read.text
    assert read.json()["job_id"] == job_id
    assert read.json()["redeemed_at"] is not None
    # Spent or not, the token itself is never handed out a second time.
    assert "token" not in read.json()


async def test_an_invitation_that_never_existed_reads_as_missing(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    await _sign_in(client)
    await _initialise(project_dir)

    response = await client.get("/api/v1/probes/enrollment/tokens/01NOTAREALID")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "enrollment.token_invalid"


# --- What the host could not do -------------------------------------------


async def test_a_probe_without_the_package_is_turned_away_with_the_reason(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The case a retirement with "uninstall MPP" leaves behind.

    Enrolment installs no package - only the bootstrap does - so a host that
    reports in without one cannot be configured. It used to run on regardless
    and die in the activate step, where the probe refused a request over a
    missing systemd unit: true, unrelated to the actual cause, and by then
    with an account and an inventory entry already left behind.
    """
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]
    # What the probe answers once the package is gone.
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=none\nservice=inactive\n"
    )

    callback = await client.post(
        f"/api/v1/enroll/{token}/callback",
        json={
            "hostname": "probe.example.test",
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"],
            "access_installed": True,
            "package_installed": False,
            "package_error": "E: Unable to locate package prtgmpprobe",
        },
    )
    assert callback.status_code == 200, callback.text
    job_id = callback.json()["job_id"]

    await _drain(_build_runner(settings, transport))

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "failed"
    assert job["error_code"] == "probe.package_missing"
    # The installer's own words, carried from the console the operator has
    # long since walked away from.
    assert "Unable to locate package" in (job["error_details"] or "")
    # Nothing was configured, so nothing was staged on the probe either.
    assert "write-config" not in transport.commands()
    # And the inventory stayed empty: starting over means a fresh invitation,
    # not cleaning up after this run first.
    assert (await client.get("/api/v1/probes")).json() == []


async def test_a_probe_that_carries_the_package_enrols_as_before(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The guard above turns away the empty probe and nobody else."""
    await _sign_in(client)
    await _initialise(project_dir)
    token = (await _invite(client))["token"]
    transport.responses["probe-info"] = (
        "OK probe-info\npackage=3.10.0-1\nservice=active\n"
    )

    callback = await client.post(
        f"/api/v1/enroll/{token}/callback",
        json={
            "hostname": "probe.example.test",
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"],
            "access_installed": True,
            "package_installed": True,
        },
    )
    job_id = callback.json()["job_id"]

    await _drain(_build_runner(settings, transport))

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "successful", job.get("error_details")
    assert "write-config" in transport.commands()
