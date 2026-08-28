"""Enrolling an iperf measurement endpoint.

The same ceremony the probes use, with one difference that runs through every
test here: the endpoint's password is generated on this side and travels over
the management channel the bootstrap installs, never through the script. So
what these tests hold is that the script carries nothing worth stealing, that
the channel is what sets the endpoint up, and that the source network - which
this platform cannot derive for a host on a public address - is never guessed.
"""

from __future__ import annotations

import base64
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.permissions import RoleName
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.iperf_helper import IperfHelperClient
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.services.events import get_broadcaster
from app.services.provisioning import ProvisioningService
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport

PASSWORD = "correct-horse-battery"

PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg\n-----END PUBLIC KEY-----\n"

ENDPOINT_INFO = """OK endpoint-info
helper=1
hostname=iperf.example.test
iperf3=3.16
service=active
port=5201
username=prtg-probe
public_key_sha256=abc123
peer=203.0.113.7
"""


def _setup_answer(public_key: str = PUBLIC_KEY) -> str:
    encoded = base64.b64encode(public_key.encode("utf-8")).decode("ascii")
    return (
        "OK endpoint-setup\n"
        "port=5201\n"
        "username=prtg-probe\n"
        f"public_key_b64={encoded}\n"
        "public_key_sha256=abc123\n"
    )


def _build_runner(
    settings: Settings, transport: ScriptedTransport
) -> tuple[JobRunner, IperfHelperClient]:
    """A runner plus the endpoint client the enrolment job will use.

    The job builds its own client from the settings unless the context already
    carries one, so the fake is put in place through the runner's context - see
    _drain below.
    """
    runner = JobRunner(
        settings=settings,
        broadcaster=get_broadcaster(),
        runtime=RuntimeFileStore(settings),
        helper=ProbeHelperClient(transport),
        catalog=SensorCatalog(settings.sensor_source_dir),
        docker=DockerAdapter(settings.docker_socket),
    )
    return runner, IperfHelperClient(transport)


async def _drain(
    runner: JobRunner, endpoints: IperfHelperClient, *, rounds: int = 12
) -> None:
    """Run the queued job to completion with the endpoint channel faked.

    JobContext builds a real SSH client on first use; patching the factory it
    calls is what keeps this test off the network without giving every other
    context a parameter it does not need.
    """
    import app.workers.context as context_module

    original = context_module.build_client
    context_module.build_client = lambda settings: endpoints  # type: ignore[assignment]
    try:
        for _ in range(rounds):
            if not await runner._claim_and_run():
                return
    finally:
        context_module.build_client = original  # type: ignore[assignment]


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


def _initialise() -> None:
    settings = get_settings()
    ProvisioningService(settings, DockerAdapter(settings)).initialise_runtime()


async def _invite(client: AsyncClient, **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": "berlin",
        "expected_host": "iperf.example.test",
        "ssh_source_cidr": "203.0.113.7/32",
    } | overrides
    response = await client.post("/api/v1/iperf-endpoints/enrollment/tokens", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- The invitation ----------------------------------------------------------


async def test_the_command_points_at_the_endpoint_script(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()

    issued = await _invite(client)

    command = issued["command"]
    # The same ceremony as a probe's, differing in one path segment: fetch the
    # CA over plain HTTP, check it against a fingerprint that came through the
    # browser, then speak TLS against it.
    assert f"/enroll/{issued['token']}/iperf-bootstrap.sh" in command
    assert issued["ca_sha256"] in command
    assert "sha256sum -c -" in command
    assert "--cacert" in command
    # As words, not as substrings: the token is part of this command and
    # secrets.token_urlsafe puts a literal "-k" in roughly one of a
    # hundred, which failed this assertion for a reason nobody could see.
    assert "-k" not in command.split() and "--insecure" not in command.split()


async def test_a_second_endpoint_cannot_take_a_name_that_is_taken(
    client: AsyncClient, project_dir: Path
) -> None:
    """The name is also the profile name on every probe.

    Two endpoints under one name would overwrite each other's credentials on
    every probe measuring against both, and the failure would look like a
    network fault months later.
    """
    await _sign_in(client)
    _initialise()
    (project_dir / "runtime" / "iperf" / "berlin.env").write_text(
        "IPERF_NAME=berlin\nIPERF_HOST=iperf.example.test\n", encoding="utf-8"
    )

    response = await client.post(
        "/api/v1/iperf-endpoints/enrollment/tokens", json={"name": "berlin"}
    )

    assert response.status_code == 409, response.text


async def test_an_invitation_without_a_source_network_is_refused(
    client: AsyncClient, project_dir: Path
) -> None:
    """No fallback to NATS_HOST_IP, unlike the probe path.

    A probe sees us under our internal address; an endpoint on a public network
    sees us under the address we leave the site with, and nothing here can
    derive that. Guessing would install a management key valid from the wrong
    network, and the only repair is a walk to that host's console - so the
    refusal happens while somebody is looking at the answer.
    """
    await _sign_in(client)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints/enrollment/tokens", json={"name": "berlin"}
    )

    assert response.status_code == 503, response.text
    assert "IPERF_SSH_SOURCE_CIDR" in response.text


async def test_the_site_default_fills_in_when_the_invitation_is_silent(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()
    env = project_dir / ".env"
    env.write_text(
        env.read_text(encoding="utf-8") + "IPERF_SSH_SOURCE_CIDR=198.51.100.0/24\n",
        encoding="utf-8",
    )

    issued = await _invite(client, ssh_source_cidr=None)

    script = await client.get(f"/api/v1/enroll/{issued['token']}/iperf-bootstrap.sh")
    assert script.status_code == 200, script.text
    assert "198.51.100.0/24" in script.text


async def test_several_source_networks_are_accepted_and_nonsense_is_not(
    client: AsyncClient, project_dir: Path
) -> None:
    """An endpoint is often reached from inside and from the outside both."""
    await _sign_in(client)
    _initialise()

    issued = await _invite(client, ssh_source_cidr="203.0.113.7/32,192.0.2.0/24")
    script = await client.get(f"/api/v1/enroll/{issued['token']}/iperf-bootstrap.sh")
    assert 'SSH_SOURCE_CIDR="203.0.113.7/32,192.0.2.0/24"' in script.text

    for bad in ("203.0.113.7/32,", "", "not-a-network/24", "203.0.113.7/33"):
        refused = await client.post(
            "/api/v1/iperf-endpoints/enrollment/tokens",
            json={"name": "hamburg", "ssh_source_cidr": bad},
        )
        assert refused.status_code == 422, f"{bad!r} was accepted"


async def test_a_bare_address_gains_its_prefix_and_a_masked_one_is_refused(
    client: AsyncClient, project_dir: Path
) -> None:
    """The two shapes that would otherwise go wrong quietly.

    Without a prefix the enrolment script refuses, three steps later, on a
    console nobody is watching. With host bits inside a prefix, masking them
    away would hand out a key valid from 254 more addresses than were typed.
    """
    await _sign_in(client)
    _initialise()

    issued = await _invite(client, ssh_source_cidr="203.0.113.7")
    script = await client.get(f"/api/v1/enroll/{issued['token']}/iperf-bootstrap.sh")
    assert 'SSH_SOURCE_CIDR="203.0.113.7/32"' in script.text

    widened = await client.post(
        "/api/v1/iperf-endpoints/enrollment/tokens",
        json={"name": "hamburg", "ssh_source_cidr": "192.0.2.5/24"},
    )
    assert widened.status_code == 422, widened.text


# --- The script --------------------------------------------------------------


async def test_the_script_carries_no_secret_and_leaves_no_placeholder(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()
    issued = await _invite(client)

    script = await client.get(f"/api/v1/enroll/{issued['token']}/iperf-bootstrap.sh")

    assert script.status_code == 200, script.text
    assert "@@" not in script.text
    # The endpoint's password is not decided yet at this point, and could not
    # be in here even if it were: fetching this does not spend the invitation,
    # so it stays readable for as long as the token lives.
    assert "IPERF3_PASSWORD" not in script.text
    assert "-----BEGIN PRIVATE KEY-----" not in script.text
    # What it does carry is public: the CA it already arrived over, and the
    # management public key it is about to authorise.
    assert "-----BEGIN CERTIFICATE-----" in script.text
    assert "ssh-ed25519" in script.text


async def test_a_probe_invitation_cannot_fetch_the_endpoint_script(
    client: AsyncClient, project_dir: Path
) -> None:
    """Each kind gets its own script, and only its own.

    The probe bootstrap installs a management user with the probe's rights. On
    a host that only measures, that is rights nobody decided to grant.
    """
    await _sign_in(client)
    _initialise()
    probe = await client.post(
        "/api/v1/probes/enrollment/tokens", json={"nats_username": "mpp-berlin"}
    )
    assert probe.status_code == 201, probe.text
    endpoint = await _invite(client)

    crossed = await client.get(
        f"/api/v1/enroll/{probe.json()['token']}/iperf-bootstrap.sh"
    )
    assert crossed.status_code == 404, crossed.text

    other_way = await client.get(f"/api/v1/enroll/{endpoint['token']}/bootstrap.sh")
    assert other_way.status_code == 404, other_way.text


async def test_the_endpoint_fetches_only_the_three_scripts_it_needs(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()
    issued = await _invite(client)

    for name in (
        "iperf-enroll.sh",
        "prtg-nats-iperf-helper",
        "setup-iperf3-endpoint.sh",
    ):
        served = await client.get(f"/api/v1/enroll/{issued['token']}/asset/{name}")
        assert served.status_code == 200, f"{name}: {served.text}"

    # The probe's assets are not on this token's list, and a path is not a name.
    for refused_name in ("enroll-probe.sh", "install-mpp.sh", "../../.env"):
        refused = await client.get(
            f"/api/v1/enroll/{issued['token']}/asset/{refused_name}"
        )
        assert refused.status_code in (400, 404), f"{refused_name} was served"


# --- The callback and the job ------------------------------------------------


async def test_the_endpoint_is_set_up_over_the_channel_and_then_recorded(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The whole point, end to end.

    Nothing is written until the endpoint has answered, the password is
    generated here rather than reported back, and the record it lands in is the
    one the shell tooling has always read.
    """
    await _sign_in(client)
    _initialise()
    issued = await _invite(client)

    transport.responses.update(
        {"endpoint-info": ENDPOINT_INFO, "endpoint-setup": _setup_answer()}
    )
    accepted = await client.post(
        f"/api/v1/enroll/{issued['token']}/iperf-callback",
        json={
            "hostname": "iperf.example.test",
            "ssh_port": 22,
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"],
            "access_installed": True,
            "platform_address": "203.0.113.7",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["job_id"]

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    # The channel was used, and used in this order: ask first, then set up.
    sent = [request.command.value for _, request in transport.calls]
    assert sent == ["endpoint-info", "endpoint-setup"]

    # The password went as payload, never as an argument - an argument would
    # stand in the endpoint's process list.
    _, setup = transport.calls[1]
    assert setup.payload and len(setup.payload) == 48
    assert setup.payload not in setup.arguments
    assert setup.arguments == ("prtg-probe", "5201")

    record = (project_dir / "runtime" / "iperf" / "berlin.env").read_text(
        encoding="utf-8"
    )
    assert "IPERF_NAME=berlin" in record
    assert "IPERF_HOST=iperf.example.test" in record
    assert "IPERF_PORT=5201" in record
    assert "IPERF_USERNAME=prtg-probe" in record
    assert "IPERF_MANAGED=true" in record
    # The password the endpoint was just given, kept where the probes' rollout
    # reads it from.
    assert f"IPERF_PASSWORD={setup.payload}" in record

    key_file = project_dir / "runtime" / "iperf" / "berlin.pem"
    assert key_file.read_text(encoding="utf-8") == PUBLIC_KEY
    assert key_file.stat().st_mode & 0o077 == 0

    listed = await client.get("/api/v1/iperf-endpoints")
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()] == ["berlin"]
    assert listed.json()[0]["managed"] is True
    assert listed.json()[0]["has_public_key"] is True


async def test_nothing_is_recorded_when_the_endpoint_refuses(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A record for an endpoint that is not set up is worse than none.

    Everything downstream believes the record: the probes would be handed a
    password the endpoint never accepted, and every sensor would report bad
    credentials against a host that is simply not ready.
    """
    await _sign_in(client)
    _initialise()
    issued = await _invite(client)

    transport.responses.update(
        {
            "endpoint-info": ENDPOINT_INFO,
            "endpoint-setup": RuntimeError("the endpoint setup failed"),
        }
    )
    accepted = await client.post(
        f"/api/v1/enroll/{issued['token']}/iperf-callback",
        json={
            "hostname": "iperf.example.test",
            "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"],
            "access_installed": True,
        },
    )
    assert accepted.status_code == 200, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    assert not (project_dir / "runtime" / "iperf" / "berlin.env").exists()
    assert not (project_dir / "runtime" / "iperf" / "berlin.pem").exists()


async def test_an_invitation_is_spent_once(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()
    issued = await _invite(client)
    body = {
        "hostname": "iperf.example.test",
        "host_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"],
    }

    first = await client.post(
        f"/api/v1/enroll/{issued['token']}/iperf-callback", json=body
    )
    assert first.status_code == 200, first.text

    # Refused the same way an invented token is, and with the same message:
    # nothing here tells a spent invitation apart from one that never existed.
    again = await client.post(
        f"/api/v1/enroll/{issued['token']}/iperf-callback", json=body
    )
    assert again.status_code == 404, again.text


async def test_a_viewer_cannot_invite_an_endpoint(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client, RoleName.VIEWER)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints/enrollment/tokens",
        json={"name": "berlin", "ssh_source_cidr": "203.0.113.7/32"},
    )

    assert response.status_code == 403, response.text
