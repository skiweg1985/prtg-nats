"""Address allocation, rendering and the guards around them.

What these pin down is the part with no second chance: an address handed to
two probes, a peer block built from a key that is not one, and an endpoint
that would route the tunnel into itself. All three are cheap to get wrong and
expensive to notice - the symptom is a probe that was fine yesterday.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ConflictError, ValidationFailedError
from app.infrastructure.overlay import (
    FIRST_PEER_INDEX,
    OverlayRuntime,
    OverlaySettings,
    generate_keypair,
    public_key_for,
    validate_mode,
    validate_public_key,
)
from app.infrastructure.runtime_files import RuntimeFileStore, overlay_address_at
from tests.conftest import write_probe_inventory


def _enable(project_dir, settings=None, **overrides: object) -> None:
    """The site's own settings in .env, the overlay's in the runtime.

    They are separate on purpose: .env sits beside the checkout on the host,
    which the API container does not have, so anything kept there is something
    an administrator has to reach a shell for.
    """
    (project_dir / ".env").write_text(
        "NATS_FQDN=nats.example.test\nNATS_HOST_IP=192.0.2.10\nNATS_PORT=23561\n",
        encoding="utf-8",
    )
    if settings is None:
        return
    values: dict[str, object] = {
        "enabled": True,
        "endpoint_host": "nats.example.test",
        "subnet": "10.83.0.0/16",
        "default_mode": "auto",
    }
    values.update(overrides)
    OverlayRuntime(settings).write_settings(OverlaySettings(**values))  # type: ignore[arg-type]


def test_the_hub_is_the_first_address_of_whatever_subnet_is_configured() -> None:
    assert overlay_address_at("10.83.0.0/16", 1) == "10.83.0.1"
    assert overlay_address_at("10.9.0.0/24", 1) == "10.9.0.1"
    # The network address is not a peer, and neither is anything past the end.
    with pytest.raises(ValidationFailedError):
        overlay_address_at("10.83.0.0/16", 0)
    with pytest.raises(ValidationFailedError):
        overlay_address_at("10.9.0.0/24", 255)


def test_a_generated_key_pair_agrees_with_itself() -> None:
    private, public = generate_keypair()
    assert validate_public_key(public) == public
    assert public_key_for(private) == public


def test_a_key_that_is_not_one_never_reaches_a_peer_block() -> None:
    with pytest.raises(ValidationFailedError):
        validate_public_key("not-a-key")
    with pytest.raises(ValidationFailedError):
        # Right shape, wrong length: 42 characters plus padding.
        validate_public_key("A" * 42 + "=")


def test_only_the_three_modes_are_modes() -> None:
    for mode in ("off", "auto", "on"):
        assert validate_mode(mode) == mode
    with pytest.raises(ValidationFailedError):
        validate_mode("sometimes")


def test_allocation_skips_addresses_already_handed_out(
    settings: Settings, project_dir
) -> None:
    _enable(project_dir, settings)
    overlay = OverlayRuntime(settings)
    runtime = RuntimeFileStore(settings)

    write_probe_inventory(project_dir, "mpp-berlin")
    write_probe_inventory(project_dir, "mpp-hamburg", host="hamburg.example.test")

    first = overlay.allocate_address()
    assert first == overlay_address_at("10.83.0.0/16", FIRST_PEER_INDEX)

    _, public = generate_keypair()
    runtime.write_probe_overlay(
        "mpp-berlin", address=first, public_key=public, mode="auto"
    )
    assert overlay.allocate_address() != first


def test_a_freed_address_is_reused_rather_than_the_range_growing(
    settings: Settings, project_dir
) -> None:
    """An installation that adds and retires probes for years should not run
    out of a /16 because of it."""
    _enable(project_dir, settings)
    overlay = OverlayRuntime(settings)
    runtime = RuntimeFileStore(settings)
    _, public = generate_keypair()

    for name in ("mpp-a", "mpp-b"):
        write_probe_inventory(project_dir, name)
    runtime.write_probe_overlay(
        "mpp-a", address=overlay.allocate_address(), public_key=public, mode="auto"
    )
    second = overlay.allocate_address()
    runtime.write_probe_overlay("mpp-b", address=second, public_key=public, mode="auto")

    runtime.clear_probe_overlay("mpp-a")
    assert overlay.allocate_address() == overlay_address_at(
        "10.83.0.0/16", FIRST_PEER_INDEX
    )
    assert second != overlay.allocate_address()


def test_the_rendered_hub_carries_one_peer_per_probe(
    settings: Settings, project_dir
) -> None:
    _enable(project_dir, settings)
    overlay = OverlayRuntime(settings)
    runtime = RuntimeFileStore(settings)
    overlay.ensure_hub_key()

    _, public = generate_keypair()
    write_probe_inventory(project_dir, "mpp-berlin")
    runtime.write_probe_overlay(
        "mpp-berlin", address="10.83.1.0", public_key=public, mode="auto"
    )
    overlay.write_hub_config()

    config = overlay.config_path.read_text(encoding="utf-8")
    assert "Address = 10.83.0.1/16" in config
    assert "ListenPort = 51820" in config
    assert f"PublicKey = {public}" in config
    # A single address, not the subnet: AllowedIPs is also the inbound filter,
    # and a peer allowed to send from any overlay address could answer for
    # another probe.
    assert "AllowedIPs = 10.83.1.0/32" in config
    assert overlay.config_path.stat().st_mode & 0o777 == 0o600


def test_a_probe_switched_off_keeps_its_peer_block(
    settings: Settings, project_dir
) -> None:
    """Its tunnel is down on the probe's side. Dropping the peer here would
    mean the hub forgetting an address it has already handed out."""
    _enable(project_dir, settings)
    overlay = OverlayRuntime(settings)
    runtime = RuntimeFileStore(settings)
    overlay.ensure_hub_key()

    _, public = generate_keypair()
    write_probe_inventory(project_dir, "mpp-berlin")
    runtime.write_probe_overlay(
        "mpp-berlin", address="10.83.1.0", public_key=public, mode="off"
    )
    assert "10.83.1.0/32" in overlay.render_hub_config()


def test_the_hub_key_is_generated_once(settings: Settings, project_dir) -> None:
    _enable(project_dir, settings)
    overlay = OverlayRuntime(settings)
    first = overlay.ensure_hub_key()
    assert overlay.ensure_hub_key() == first
    assert overlay.hub_key_path.stat().st_mode & 0o777 == 0o600


def test_an_endpoint_that_is_the_nats_address_is_refused(
    settings: Settings, project_dir
) -> None:
    """Routing NATS_HOST_IP through a tunnel whose endpoint is that address
    would put the tunnel inside itself, and the probe loses both paths."""
    _enable(project_dir, settings, endpoint_host="192.0.2.10")
    with pytest.raises(ValidationFailedError):
        OverlayRuntime(settings).check_endpoint_collision()


def test_a_full_subnet_is_a_conflict_not_a_crash(
    settings: Settings, project_dir
) -> None:
    _enable(project_dir, settings, subnet="10.83.0.0/30")
    overlay = OverlayRuntime(settings)
    with pytest.raises(ConflictError):
        overlay.allocate_address()


def test_the_inventory_keeps_the_peer_through_a_reconfiguration(
    settings: Settings, project_dir
) -> None:
    """write_probe_inventory rewrites the whole file. Losing the key here
    would strand a probe behind its own tunnel."""
    _enable(project_dir, settings)
    runtime = RuntimeFileStore(settings)
    _, public = generate_keypair()

    write_probe_inventory(project_dir, "mpp-berlin")
    runtime.write_probe_overlay(
        "mpp-berlin", address="10.83.1.0", public_key=public, mode="on"
    )
    runtime.write_probe_inventory(
        nats_username="mpp-berlin",
        ssh_host="new.example.test",
        probe_id="11111111-2222-3333-4444-555555555555",
        access_key="ACCESSKEY123",
        probe_name="Example Probe",
    )

    probe = runtime.read_probe("mpp-berlin")
    assert probe.ssh_host == "new.example.test"
    assert probe.overlay_address == "10.83.1.0"
    assert probe.overlay_public_key == public
    assert probe.overlay_mode == "on"


def test_the_overlay_address_is_dialled_first_but_never_alone(
    settings: Settings, project_dir
) -> None:
    _enable(project_dir, settings)
    runtime = RuntimeFileStore(settings)
    _, public = generate_keypair()
    write_probe_inventory(project_dir, "mpp-berlin", host="berlin.example.test")

    plain = runtime.read_probe("mpp-berlin")
    assert plain.management_hosts == ("berlin.example.test",)

    runtime.write_probe_overlay(
        "mpp-berlin", address="10.83.1.0", public_key=public, mode="auto"
    )
    assert runtime.read_probe("mpp-berlin").management_hosts == (
        "10.83.1.0",
        "berlin.example.test",
    )

    # Off means the tunnel is not there to be dialled.
    runtime.write_probe_overlay(
        "mpp-berlin", address="10.83.1.0", public_key=public, mode="off"
    )
    assert runtime.read_probe("mpp-berlin").management_hosts == ("berlin.example.test",)


async def test_the_ordinary_address_is_tried_when_the_tunnel_does_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The two addresses fail independently, and a probe reachable by either
    one is not a lost probe."""
    from app.core.errors import ProbeRejectedError, ProbeUnreachableError
    from app.infrastructure.probe_helper import (
        HelperRequest,
        ProbeConnection,
        SshHelperTransport,
    )
    from app.infrastructure.probe_helper.protocol import HelperCommand

    key = tmp_path / "key"
    key.write_text("not a real key", encoding="utf-8")
    transport = SshHelperTransport(
        key_path=key, known_hosts_path=tmp_path / "known_hosts"
    )
    attempted: list[str] = []

    async def only_the_second_answers(self, connection, host, request, timeout) -> str:
        attempted.append(host)
        if host == "10.83.1.0":
            raise ProbeUnreachableError.of(connection.label, details="no route")
        return "OK probe-info\n"

    monkeypatch.setattr(SshHelperTransport, "_run_against", only_the_second_answers)
    connection = ProbeConnection(
        nats_username="mpp-berlin",
        host="10.83.1.0",
        fallback_host="berlin.example.test",
    )
    answer = await transport.run(
        connection, HelperRequest(command=HelperCommand.PROBE_INFO), 30
    )
    assert answer.startswith("OK probe-info")
    assert attempted == ["10.83.1.0", "berlin.example.test"]

    # A refusal is an answer, not a reason to send the same request twice.
    attempted.clear()

    async def refuse(self, connection, host, request, timeout) -> str:
        attempted.append(host)
        raise ProbeRejectedError(params={"probe": connection.label}, details="no")

    monkeypatch.setattr(SshHelperTransport, "_run_against", refuse)
    with pytest.raises(ProbeRejectedError):
        await transport.run(
            connection, HelperRequest(command=HelperCommand.PROBE_INFO), 30
        )
    assert attempted == ["10.83.1.0"]
