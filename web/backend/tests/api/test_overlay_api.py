"""The overlay over HTTP.

What matters here is the same thing the page shows: an installation that has
not turned the overlay on has to say so rather than render an empty hub, and a
peer's mode has to be visible next to what the probe is actually doing with
it - the two are not the same answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.permissions import RoleName
from app.infrastructure.overlay import (
    OverlayRuntime,
    OverlaySettings,
    generate_keypair,
)
from app.infrastructure.runtime_files import RuntimeFileStore
from tests.conftest import write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"


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


def write_site(project_dir: Path) -> None:
    (project_dir / ".env").write_text(
        "NATS_FQDN=nats.example.test\nNATS_HOST_IP=192.0.2.10\nNATS_PORT=23561\n",
        encoding="utf-8",
    )


def enable_overlay(project_dir: Path, settings: Settings) -> None:
    """Straight into the runtime, the way the enable endpoint writes it."""
    write_site(project_dir)
    OverlayRuntime(settings).write_settings(
        OverlaySettings(
            enabled=True,
            endpoint_host="nats.example.test",
            subnet="10.83.0.0/16",
            default_mode="auto",
        )
    )


async def initialise_runtime() -> None:
    """A runtime with a CA and a management key, which an invitation needs."""
    from app.core.config import get_settings
    from app.infrastructure.docker import DockerAdapter
    from app.services.provisioning import ProvisioningService

    settings = get_settings()
    ProvisioningService(settings, DockerAdapter(settings)).initialise_runtime()


async def test_an_installation_without_the_overlay_says_so(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)

    answer = await client.get("/api/v1/overlay")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["enabled"] is False
    assert body["peers"] == []
    # Derived even while it is off, so the page can name the address range a
    # reader would be opting into.
    assert body["hub_address"] == "10.83.0.1"


async def test_a_peer_reports_its_mode_and_the_path_it_is_on(
    client: AsyncClient, settings: Settings, project_dir: Path
) -> None:
    enable_overlay(project_dir, settings)
    write_probe_inventory(project_dir, PROBE)
    _, public = generate_keypair()
    RuntimeFileStore(settings).write_probe_overlay(
        PROBE,
        address="10.83.1.0",
        public_key=public,
        mode="auto",
        # A probe in auto that is on the tunnel is working and means somebody's
        # ordinary route is down. The two have to be separately visible.
        last_state="tunnel",
    )
    OverlayRuntime(settings).ensure_hub_key()
    await sign_in(client)

    answer = await client.get("/api/v1/overlay")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["enabled"] is True
    assert body["endpoint"] == "nats.example.test:51820"
    peer = body["peers"][0]
    assert peer["nats_username"] == PROBE
    assert peer["address"] == "10.83.1.0"
    assert peer["mode"] == "auto"
    assert peer["last_state"] == "tunnel"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/overlay/peers",
        "/api/v1/overlay/peers/mode",
        "/api/v1/overlay/peers/remove",
    ],
)
async def test_changing_the_overlay_needs_more_than_reading_it(
    client: AsyncClient, settings: Settings, project_dir: Path, path: str
) -> None:
    enable_overlay(project_dir, settings)
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client, RoleName.VIEWER)

    listing = await client.get("/api/v1/overlay")
    assert listing.status_code == 200, listing.text

    refused = await client.post(path, json={"probe_ids": ["X"], "mode": "off"})
    assert refused.status_code == 403, refused.text


async def test_an_action_on_an_unknown_probe_fails_before_a_job_exists(
    client: AsyncClient, settings: Settings, project_dir: Path
) -> None:
    enable_overlay(project_dir, settings)
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)

    answer = await client.post(
        "/api/v1/overlay/peers", json={"probe_ids": ["not-a-probe"]}
    )
    assert answer.status_code == 404, answer.text

    jobs = await client.get("/api/v1/jobs")
    assert jobs.json() == []


async def test_an_invitation_reserves_an_address_before_the_probe_exists(
    client: AsyncClient, settings: Settings, project_dir: Path
) -> None:
    """A probe behind NAT reports in once and is never reachable the other
    way, so its address has to be in the script it is handed."""
    enable_overlay(project_dir, settings)
    await sign_in(client)
    await initialise_runtime()

    first = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin-01", "expected_host": "192.0.2.20"},
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin-02", "expected_host": "192.0.2.21"},
    )
    assert second.status_code == 201, second.text

    script = await client.get(f"/api/v1/enroll/{first.json()['token']}/bootstrap.sh")
    assert script.status_code == 200, script.text
    body = script.text
    assert 'OVERLAY_ENABLED="true"' in body
    assert 'OVERLAY_MODE="auto"' in body
    assert 'OVERLAY_ENDPOINT="nats.example.test:51820"' in body
    # Through the helper it just installed, so there is one implementation of
    # configuring the tunnel rather than two.
    assert "overlay-configure" in body
    # The hub is added to the from= list, and the address the operator would
    # otherwise be reached from is still in it.
    assert 'SSH_SOURCE_CIDR="192.0.2.10/32,10.83.0.1/32"' in body

    other = await client.get(f"/api/v1/enroll/{second.json()['token']}/bootstrap.sh")
    assert other.status_code == 200, other.text

    def address_of(script_body: str) -> str:
        line = next(
            row
            for row in script_body.splitlines()
            if row.startswith("OVERLAY_ADDRESS=")
        )
        return line.split('"')[1]

    # Two open invitations must not be promised the same address: the second
    # probe to report in would take the first one's tunnel down.
    assert address_of(body) != address_of(other.text)


async def test_without_an_overlay_the_script_says_so_rather_than_half_configuring(
    client: AsyncClient, project_dir: Path
) -> None:
    write_site(project_dir)
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await initialise_runtime()

    issued = await client.post(
        "/api/v1/probes/enrollment/tokens",
        json={"nats_username": "mpp-berlin-02", "expected_host": "192.0.2.20"},
    )
    assert issued.status_code == 201, issued.text

    script = await client.get(f"/api/v1/enroll/{issued.json()['token']}/bootstrap.sh")
    assert script.status_code == 200, script.text
    assert 'OVERLAY_ENABLED="false"' in script.text
    # No leftover placeholder reaches a root shell.
    assert "@@" not in script.text


async def test_enabling_is_a_request_and_not_a_shell_session(
    client: AsyncClient, settings: Settings, project_dir: Path
) -> None:
    """The whole point of moving the switch out of .env: an administrator
    turns the overlay on from the interface, and the runtime records it."""
    write_site(project_dir)
    await sign_in(client)
    await initialise_runtime()

    before = await client.get("/api/v1/overlay")
    assert before.json()["enabled"] is False

    answer = await client.post(
        "/api/v1/overlay/enable", json={"endpoint_host": "nats.example.com"}
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["enabled"] is True
    assert body["endpoint"] == "nats.example.com:51820"
    assert body["hub_public_key"]

    # In the runtime, not in .env - the file the API container cannot write.
    assert "OVERLAY" not in (project_dir / ".env").read_text(encoding="utf-8")
    recorded = OverlayRuntime(settings).settings()
    assert recorded.enabled is True
    assert recorded.endpoint_host == "nats.example.com"


async def test_disabling_keeps_every_peer(
    client: AsyncClient, settings: Settings, project_dir: Path
) -> None:
    """Turning the overlay off is not retiring every probe from it."""
    enable_overlay(project_dir, settings)
    write_probe_inventory(project_dir, PROBE)
    _, public = generate_keypair()
    RuntimeFileStore(settings).write_probe_overlay(
        PROBE, address="10.83.1.0", public_key=public, mode="auto"
    )
    OverlayRuntime(settings).ensure_hub_key()
    await sign_in(client)

    answer = await client.post("/api/v1/overlay/disable")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["enabled"] is False
    assert [peer["address"] for peer in body["peers"]] == ["10.83.1.0"]


async def test_an_endpoint_that_is_the_nats_address_is_refused_at_the_door(
    client: AsyncClient, project_dir: Path
) -> None:
    """No recovering from this one on the far side, so it never gets written."""
    write_site(project_dir)
    await sign_in(client)
    await initialise_runtime()

    answer = await client.post(
        "/api/v1/overlay/enable", json={"endpoint_host": "192.0.2.10"}
    )
    assert answer.status_code == 422, answer.text
    assert (await client.get("/api/v1/overlay")).json()["enabled"] is False


async def test_only_an_administrator_may_turn_the_overlay_on(
    client: AsyncClient, project_dir: Path
) -> None:
    """Same decision system.update guards: whoever presses it decides that a
    container with network-admin rights runs on this host."""
    write_site(project_dir)
    await sign_in(client, RoleName.OPERATOR)

    refused = await client.post(
        "/api/v1/overlay/enable", json={"endpoint_host": "nats.example.com"}
    )
    assert refused.status_code == 403, refused.text

    # Moving a probe between the tunnel and the direct path stays an operator's
    # job; only the switch itself is administrator-only.
    assert (await client.get("/api/v1/overlay")).status_code == 200
