"""Setting an iperf endpoint up by reaching out to it, and taking it back off.

The push path exists because the bootstrap one assumes something the topology
usually does not give: that the endpoint can reach this installation. It cannot,
when it stands on a public address and the platform does not. What it can do is
answer an SSH connection - which it has to anyway, or the management channel
would not work at all.

So these tests hold the things that follow from that. The administrator
credentials never reach the job payload. The host keys are accepted before they
travel. And removal takes the probes' credentials away before the endpoint stops
accepting them, because the other order strands the fleet.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings
from app.core.permissions import RoleName
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.iperf_helper import IperfHelperClient
from app.infrastructure.known_hosts import HostKey
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.services.events import get_broadcaster
from app.services.provisioning import ProvisioningService
from app.workers.job_runner import JobRunner
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor

PASSWORD = "correct-horse-battery"
PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg\n-----END PUBLIC KEY-----\n"

HOST_KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"

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


def _setup_answer() -> str:
    encoded = base64.b64encode(PUBLIC_KEY.encode("utf-8")).decode("ascii")
    return (
        f"OK endpoint-setup\nport=5201\nusername=prtg-probe\npublic_key_b64={encoded}\n"
    )


class _Report:
    def __init__(self) -> None:
        self.lines = ["staged", "installed"]


class RecordingInstaller:
    """Stands in for the one outbound SSH connection.

    Records what it was asked to do, so a test can assert on the credentials
    and the command without a host to sign in to.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failure: Exception | None = None

    async def __call__(self, **kwargs: Any) -> _Report:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return _Report()


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch) -> RecordingInstaller:
    recorder = RecordingInstaller()
    monkeypatch.setattr(
        "app.workers.handlers.iperf_provisioning.install_access", recorder
    )
    return recorder


@pytest.fixture
def scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _scan(host: str, port: int = 22, **_: Any) -> tuple[HostKey, ...]:
        key = HostKey.parse(HOST_KEY_LINE)
        assert key is not None
        return (key,)

    monkeypatch.setattr("app.api.v1.routes.iperf.scan_host_keys", _scan)


def _build_runner(
    settings: Settings, transport: ScriptedTransport
) -> tuple[JobRunner, IperfHelperClient]:
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


async def _provision(client: AsyncClient, **overrides: Any) -> dict:
    body: dict[str, Any] = {
        "name": "berlin",
        "host": "iperf.example.test",
        "ssh_source_cidr": "203.0.113.7/32",
        "host_keys": [HOST_KEY_LINE],
        "admin": {"username": "ops", "password": "hunter2"},
    } | overrides
    response = await client.post("/api/v1/iperf-endpoints", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def _write_endpoint(
    project_dir: Path, name: str = "berlin", *, managed: bool = True
) -> None:
    (project_dir / "runtime" / "iperf" / f"{name}.env").write_text(
        f"IPERF_NAME={name}\n"
        "IPERF_HOST=iperf.example.test\n"
        "IPERF_PORT=5201\n"
        "IPERF_USERNAME=prtg-probe\n"
        "IPERF_PASSWORD=oldpassword\n"
        f"IPERF_MANAGED={'true' if managed else 'false'}\n"
        "IPERF_SSH_PORT=22\n",
        encoding="utf-8",
    )
    (project_dir / "runtime" / "iperf" / f"{name}.pem").write_text(
        PUBLIC_KEY, encoding="utf-8"
    )


# --- Accepting the host ------------------------------------------------------


async def test_the_scan_reports_keys_with_their_fingerprints(
    client: AsyncClient, project_dir: Path, scanner: None
) -> None:
    """Its own step, before any credential travels to that address."""
    await _sign_in(client)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints/host-keys",
        json={"host": "iperf.example.test", "ssh_port": 22},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keys"][0]["algorithm"] == "ssh-ed25519"
    assert body["keys"][0]["fingerprint"].startswith("SHA256:")
    assert body["already_pinned"] is False


async def test_a_viewer_cannot_make_the_server_connect_somewhere(
    client: AsyncClient, project_dir: Path, scanner: None
) -> None:
    """The scan reads nothing here but makes this server talk to an address a
    caller named, which is why it needs the manage permission."""
    await _sign_in(client, RoleName.VIEWER)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints/host-keys", json={"host": "iperf.example.test"}
    )

    assert response.status_code == 403, response.text


# --- The push ----------------------------------------------------------------


async def test_the_admin_credentials_never_reach_the_job_payload(
    client: AsyncClient, project_dir: Path, installer: RecordingInstaller
) -> None:
    """The payload is a database row. A password in it would outlive the run."""
    await _sign_in(client)
    _initialise()

    accepted = await _provision(client)

    job = await client.get(f"/api/v1/jobs/{accepted['job_id']}")
    assert job.status_code == 200, job.text
    serialised = job.text
    assert "hunter2" not in serialised
    assert "admin" not in job.json()["payload"]
    # What is in there is what a reviewer needs: where it went and what it may
    # be reached from.
    assert job.json()["payload"]["host"] == "iperf.example.test"
    assert job.json()["payload"]["ssh_source_cidr"] == "203.0.113.7/32"


async def test_the_push_pins_first_then_installs_then_sets_up(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
    installer: RecordingInstaller,
) -> None:
    await _sign_in(client)
    _initialise()
    transport.responses.update(
        {"endpoint-info": ENDPOINT_INFO, "endpoint-setup": _setup_answer()}
    )

    await _provision(client)
    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    # The host key is pinned before the sign-in, which is what makes the
    # operator's acceptance count for anything.
    pinned = settings.ssh_known_hosts_path.read_text(encoding="utf-8")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIExample" in pinned

    assert len(installer.calls) == 1
    call = installer.calls[0]
    assert call["credentials"].username == "ops"
    assert call["credentials"].password == "hunter2"
    # The enrolment script is what installs the restricted access, and the
    # source network is not optional on that command line.
    assert "--source-cidr" in call["command"]
    assert "203.0.113.7/32" in call["command"]
    assert set(call["files"]) == {
        "iperf-enroll.sh",
        "prtg-nats-iperf-helper",
        "setup-iperf3-endpoint.sh",
        "management.pub",
    }

    # And then the same finish as the bootstrap path.
    assert [request.command.value for _, request in transport.calls] == [
        "endpoint-info",
        "endpoint-setup",
    ]
    record = (project_dir / "runtime" / "iperf" / "berlin.env").read_text(
        encoding="utf-8"
    )
    assert "IPERF_MANAGED=true" in record
    assert "IPERF_SSH_PORT=22" in record


async def test_a_failed_sign_in_leaves_no_record(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
    installer: RecordingInstaller,
) -> None:
    await _sign_in(client)
    _initialise()
    installer.failure = RuntimeError("permission denied")

    await _provision(client)
    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    assert not (project_dir / "runtime" / "iperf" / "berlin.env").exists()


async def test_a_sign_in_without_any_credential_is_refused(
    client: AsyncClient, project_dir: Path, installer: RecordingInstaller
) -> None:
    await _sign_in(client)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints",
        json={
            "name": "berlin",
            "host": "iperf.example.test",
            "ssh_source_cidr": "203.0.113.7/32",
            "host_keys": [HOST_KEY_LINE],
            "admin": {"username": "ops"},
        },
    )

    assert response.status_code == 422, response.text


# --- Registering one somebody else operates ----------------------------------


async def test_a_foreign_endpoint_is_recorded_without_touching_anything(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()

    response = await client.post(
        "/api/v1/iperf-endpoints/register",
        json={
            "name": "provider",
            "host": "iperf.provider.example",
            "port": 5201,
            "username": "customer",
            "password": "given-to-us",
            "public_key_pem": PUBLIC_KEY,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["managed"] is False
    record = (project_dir / "runtime" / "iperf" / "provider.env").read_text(
        encoding="utf-8"
    )
    assert "IPERF_MANAGED=false" in record


async def test_credentials_for_a_foreign_endpoint_are_all_or_nothing(
    client: AsyncClient, project_dir: Path
) -> None:
    """Half the credentials is a sensor that fails on every single run."""
    await _sign_in(client)
    _initialise()

    without_password = await client.post(
        "/api/v1/iperf-endpoints/register",
        json={"name": "a", "host": "h.example", "username": "customer"},
    )
    assert without_password.status_code == 422, without_password.text

    without_key = await client.post(
        "/api/v1/iperf-endpoints/register",
        json={
            "name": "b",
            "host": "h.example",
            "username": "customer",
            "password": "x",
        },
    )
    assert without_key.status_code == 422, without_key.text


async def test_a_foreign_endpoint_cannot_be_rotated_from_here(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir, "provider", managed=False)

    response = await client.post("/api/v1/iperf-endpoints/provider/rotate")

    assert response.status_code == 409, response.text


# --- Rotation ----------------------------------------------------------------


async def test_rotation_sets_a_new_password_and_carries_it_to_the_probes(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The second half is not a follow-up: without it the probes are locked
    out by the very change that was meant to be routine."""
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))
    (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").write_text(
        "berlin\n", encoding="utf-8"
    )
    transport.responses.update({"endpoint-setup": _setup_answer()})

    accepted = await client.post("/api/v1/iperf-endpoints/berlin/rotate")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    sent = [request.command.value for _, request in transport.calls]
    assert "endpoint-setup" in sent
    assert "sensor-write-profile" in sent

    # The profile carries the whole endpoint, not only its credentials. That is
    # what lets a PRTG sensor say "--profile berlin" and nothing else - with
    # the address configured separately, the two could name different endpoints
    # and the run would fail to authenticate for no visible reason.
    written = next(
        request
        for _, request in transport.calls
        if request.command.value == "sensor-write-profile"
    )
    assert written.payload is not None
    assert "IPERF3_HOST=iperf.example.test" in written.payload
    assert "IPERF3_PORT=5201" in written.payload
    assert "IPERF3_USERNAME=prtg-probe" in written.payload
    assert "IPERF3_PASSWORD=" in written.payload
    assert "IPERF3_PUBLIC_KEY_B64=" in written.payload

    record = (project_dir / "runtime" / "iperf" / "berlin.env").read_text(
        encoding="utf-8"
    )
    assert "IPERF_PASSWORD=oldpassword" not in record
    # The key pair on the endpoint is untouched by a credential change, so the
    # stored copy has to survive it - without it no probe could encrypt again.
    assert (project_dir / "runtime" / "iperf" / "berlin.pem").read_text(
        encoding="utf-8"
    ) == PUBLIC_KEY


# --- Removal -----------------------------------------------------------------


async def test_removal_takes_the_probes_first_and_the_record_last(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The reverse order would strand the fleet.

    A failure after the endpoint stops accepting credentials, but before the
    probes lose them, leaves sensors measuring against a host that refuses them
    and no way left to tell the probes about it.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))
    (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").write_text(
        "berlin\n", encoding="utf-8"
    )

    accepted = await client.delete("/api/v1/iperf-endpoints/berlin")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    sent = [request.command.value for _, request in transport.calls]
    assert sent.index("sensor-remove-profile") < sent.index("endpoint-remove")
    assert sent.index("endpoint-remove") < sent.index("unenroll")

    assert not (project_dir / "runtime" / "iperf" / "berlin.env").exists()
    assert not (project_dir / "runtime" / "iperf" / "berlin.pem").exists()
    assert not (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").exists()


async def test_an_unreachable_endpoint_can_still_be_forgotten(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """A machine decommissioned last week still has a record here.

    Refusing to clean that up because the host does not answer would be a
    record nobody can ever remove.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    transport.responses.update(
        {
            "endpoint-remove": RuntimeError("host is gone"),
            "unenroll": RuntimeError("gone"),
        }
    )

    accepted = await client.delete("/api/v1/iperf-endpoints/berlin")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    assert not (project_dir / "runtime" / "iperf" / "berlin.env").exists()


async def test_keep_service_leaves_the_endpoint_running(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)

    accepted = await client.delete("/api/v1/iperf-endpoints/berlin?keep_service=true")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    sent = [request.command.value for _, request in transport.calls]
    assert "endpoint-remove" not in sent
    assert not (project_dir / "runtime" / "iperf" / "berlin.env").exists()


async def test_removing_an_endpoint_that_was_never_registered_is_a_404(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _initialise()

    response = await client.delete("/api/v1/iperf-endpoints/nowhere")

    assert response.status_code == 404, response.text


async def test_rotation_also_refreshes_the_default_alias(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The alias is the profile the sensors actually use, and it was missed.

    With one endpoint, "default" is what makes --profile unnecessary - so it is
    the profile every PRTG sensor object on this probe reads. Refreshing only
    the one named after the endpoint left that alias on the old password and
    locked out exactly the probes the rotation was meant to keep running, with
    nothing to see but a sensor going red some minutes later.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))
    (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").write_text(
        "berlin\n", encoding="utf-8"
    )
    transport.responses.update({"endpoint-setup": _setup_answer()})

    accepted = await client.post("/api/v1/iperf-endpoints/berlin/rotate")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    written = {
        request.arguments[1]: request.payload
        for _, request in transport.calls
        if request.command.value == "sensor-write-profile"
    }
    assert set(written) == {"berlin", "default"}
    # Byte for byte: the alias is the same profile under a second name, and a
    # difference between the two is the bug this test exists for.
    assert written["default"] == written["berlin"]


async def test_removal_takes_the_default_alias_off_the_probes_too(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Left behind, it names a host that no longer answers.

    Worse than a missing profile: the sensor finds credentials, tries, and
    reports a failed measurement rather than an endpoint that is gone.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))
    (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").write_text(
        "berlin\n", encoding="utf-8"
    )

    accepted = await client.delete("/api/v1/iperf-endpoints/berlin")
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    removed = [
        request.arguments[1]
        for _, request in transport.calls
        if request.command.value == "sensor-remove-profile"
    ]
    assert removed == ["berlin", "default"]


# --- Deploying and revoking per probe ----------------------------------------


async def test_credentials_reach_only_the_probes_that_were_named(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """The operation the interface had no way to perform.

    Widening what a probe holds used to mean rolling the whole sensor out
    again, and narrowing it meant a terminal - which is why the "deployed to"
    column could be read but not changed.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))
    write_probe_inventory(project_dir, "mpp-hamburg", sensors=("iperf-throughput",))

    accepted = await client.post(
        "/api/v1/iperf-endpoints/berlin/deploy", json={"probes": ["mpp-berlin"]}
    )
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    assert {label for label, _ in transport.calls} == {"mpp-berlin"}
    assert (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").read_text(
        encoding="utf-8"
    ) == "berlin\n"
    assert not (project_dir / "runtime" / "probes" / "mpp-hamburg.iperf").exists()

    listed = await client.get("/api/v1/iperf-endpoints")
    assert listed.status_code == 200, listed.text
    assert [holder["probe"] for holder in listed.json()[0]["holders"]] == ["mpp-berlin"]


async def test_revoking_leaves_the_endpoint_and_the_other_probes_alone(
    client: AsyncClient,
    project_dir: Path,
    transport: ScriptedTransport,
) -> None:
    """Only this probe stops measuring - and stays stopped.

    The assignment is what the next sensor rollout reads, so a revoke that
    removed the file but not the claim would be undone by the next deployment.
    """
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    for probe in ("mpp-berlin", "mpp-hamburg"):
        write_probe_inventory(project_dir, probe, sensors=("iperf-throughput",))
        (project_dir / "runtime" / "probes" / f"{probe}.iperf").write_text(
            "berlin\n", encoding="utf-8"
        )

    accepted = await client.post(
        "/api/v1/iperf-endpoints/berlin/revoke", json={"probes": ["mpp-hamburg"]}
    )
    assert accepted.status_code == 202, accepted.text

    settings = get_settings()
    runner, endpoints = _build_runner(settings, transport)
    await _drain(runner, endpoints)

    # The alias goes with it: hamburg holds nothing afterwards, so "default"
    # would name an endpoint this probe may no longer measure against.
    removed = [
        request.arguments[1]
        for _, request in transport.calls
        if request.command.value == "sensor-remove-profile"
    ]
    assert removed == ["berlin", "default"]

    assert not (project_dir / "runtime" / "probes" / "mpp-hamburg.iperf").exists()
    assert (project_dir / "runtime" / "probes" / "mpp-berlin.iperf").read_text(
        encoding="utf-8"
    ) == "berlin\n"
    assert (project_dir / "runtime" / "iperf" / "berlin.env").exists()


async def test_a_probe_that_is_not_enrolled_is_refused_rather_than_skipped(
    client: AsyncClient, project_dir: Path
) -> None:
    """A job that quietly did less than it was asked leaves nobody to notice."""
    await _sign_in(client)
    _initialise()
    _write_endpoint(project_dir)
    write_sensor(project_dir, "iperf-throughput", iperf_kind="iperf3")
    write_probe_inventory(project_dir, "mpp-berlin", sensors=("iperf-throughput",))

    refused = await client.post(
        "/api/v1/iperf-endpoints/berlin/deploy",
        json={"probes": ["mpp-berlin", "mpp-typo"]},
    )
    assert refused.status_code == 404, refused.text


async def test_a_viewer_cannot_hand_credentials_to_a_probe(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client, RoleName.VIEWER)
    _initialise()
    _write_endpoint(project_dir)

    refused = await client.post(
        "/api/v1/iperf-endpoints/berlin/deploy", json={"probes": ["mpp-berlin"]}
    )
    assert refused.status_code == 403, refused.text


# --- What PRTG needs, per probe ----------------------------------------------


async def test_a_lone_endpoint_needs_no_parameter_in_prtg(
    client: AsyncClient, project_dir: Path
) -> None:
    """One endpoint on a probe means the "default" alias, and nothing to paste.

    The sensor reads address, port and user out of that profile, so a sensor
    object in PRTG carries only what it measures - not where.
    """
    await _sign_in(client)
    _write_endpoint(project_dir, "berlin")
    write_probe_inventory(project_dir, "mpp-berlin", endpoints=("berlin",))

    listed = await client.get("/api/v1/iperf-endpoints")
    assert listed.status_code == 200, listed.text
    (holder,) = listed.json()[0]["holders"]
    assert holder == {
        "probe": "mpp-berlin",
        "endpoints_held": 1,
        "uses_default_alias": True,
        "parameter_line": "",
    }


async def test_a_second_endpoint_makes_the_profile_mandatory(
    client: AsyncClient, project_dir: Path
) -> None:
    """The line belongs to the pair, not to the endpoint.

    The same endpoint answers differently for a probe that holds it alone and
    for one that holds two - because on the second the alias is gone and every
    sensor object has to name what it measures against.
    """
    await _sign_in(client)
    _write_endpoint(project_dir, "berlin")
    _write_endpoint(project_dir, "hamburg")
    write_probe_inventory(project_dir, "mpp-both", endpoints=("berlin", "hamburg"))
    write_probe_inventory(project_dir, "mpp-lone", endpoints=("berlin",))

    listed = await client.get("/api/v1/iperf-endpoints")
    endpoints = {entry["name"]: entry for entry in listed.json()}

    holders = {holder["probe"]: holder for holder in endpoints["berlin"]["holders"]}
    assert holders["mpp-both"]["endpoints_held"] == 2
    assert holders["mpp-both"]["uses_default_alias"] is False
    assert holders["mpp-both"]["parameter_line"] == "--profile berlin"
    # Same endpoint, other probe, other answer.
    assert holders["mpp-lone"]["uses_default_alias"] is True
    assert holders["mpp-lone"]["parameter_line"] == ""

    (hamburg,) = endpoints["hamburg"]["holders"]
    assert hamburg["parameter_line"] == "--profile hamburg"


async def test_a_name_only_the_probe_remembers_is_no_holder(
    client: AsyncClient, project_dir: Path
) -> None:
    """The sidecar outlives a removed endpoint; the answer must not.

    It also must not count towards the alias: a probe holding one registered
    endpoint and one forgotten name still carries "default" for the one that
    is left, which is exactly what the rollout does on the probe.
    """
    await _sign_in(client)
    _write_endpoint(project_dir, "berlin")
    write_probe_inventory(project_dir, "mpp-berlin", endpoints=("berlin", "retired"))

    listed = await client.get("/api/v1/iperf-endpoints")
    assert [entry["name"] for entry in listed.json()] == ["berlin"]
    (holder,) = listed.json()[0]["holders"]
    assert holder["endpoints_held"] == 1
    assert holder["uses_default_alias"] is True


async def test_an_endpoint_can_be_read_on_its_own(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    _write_endpoint(project_dir, "berlin")
    write_probe_inventory(project_dir, "mpp-berlin", endpoints=("berlin",))

    one = await client.get("/api/v1/iperf-endpoints/berlin")
    assert one.status_code == 200, one.text
    listed = await client.get("/api/v1/iperf-endpoints")
    assert one.json() == listed.json()[0]

    # A page opened on an endpoint that was removed has to say so, rather than
    # render itself empty.
    missing = await client.get("/api/v1/iperf-endpoints/nowhere")
    assert missing.status_code == 404, missing.text
