"""ADR 0011: a probe is reached by identity, not by the address it had
yesterday.

The routes come from the inventory; a probe reached over the overlay reports
the address it holds now, and that becomes the fallback - but only where the
answer leaves no doubt about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.infrastructure.probe_helper.client import ProbeConnection
from app.infrastructure.probe_helper.management_transport import (
    inventory_route_observer,
)
from app.infrastructure.probe_helper.protocol import (
    HelperCommand,
    HelperRequest,
    parse_response,
    reported_addresses,
)
from app.infrastructure.runtime_files import RuntimeFileStore


def _store(settings: Settings, **values: str) -> RuntimeFileStore:
    settings.probe_dir.mkdir(parents=True, exist_ok=True)
    lines = {
        "NATS_USERNAME": "mpp-a",
        "SSH_HOST": "198.51.100.29",
        "SSH_PORT": "22",
        **values,
    }
    (settings.probe_dir / "mpp-a.env").write_text(
        "".join(f"{key}={value}\n" for key, value in lines.items()),
        encoding="utf-8",
    )
    return RuntimeFileStore(settings)


def _with_overlay(settings: Settings) -> RuntimeFileStore:
    return _store(
        settings,
        OVERLAY_ADDRESS="10.99.0.2",
        OVERLAY_PUBLIC_KEY="cHVibGlj",
        OVERLAY_MODE="auto",
    )


def _info(addresses: str) -> tuple[str, ...]:
    """What the observer hands the store, parsed the way the protocol does."""
    return reported_addresses(
        parse_response(
            f"OK probe-info\nservice=active\naddresses={addresses}\n",
            expected=HelperCommand.PROBE_INFO,
        )
    )


def test_the_connection_verifies_the_key_against_the_probe(
    settings: Settings,
) -> None:
    connection = ProbeConnection.for_probe(_store(settings).read_probe("mpp-a"))
    assert connection.host_key_alias == "prtg-nats-probe-mpp-a"


def test_a_probe_reached_over_the_tunnel_brings_its_new_address(
    settings: Settings,
) -> None:
    store = _with_overlay(settings)

    adopted = store.adopt_reported_address("mpp-a", "10.99.0.2", _info("198.51.100.40"))

    assert adopted == "198.51.100.40"
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.40"
    assert store.read_probe("mpp-a").overlay_mode == "auto"


def test_an_answer_over_the_site_address_keeps_it(settings: Settings) -> None:
    """Behind NAT the probe sees its private address while the platform reaches
    the public one; the route that just answered must not be replaced."""
    store = _with_overlay(settings)

    adopted = store.adopt_reported_address(
        "mpp-a", "198.51.100.29", _info("203.0.113.20")
    )

    assert adopted is None
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.29"


@pytest.mark.parametrize(
    "addresses",
    ["198.51.100.41,198.51.100.42", "none", "", "not an address!"],
)
def test_an_answer_that_leaves_doubt_changes_nothing(
    settings: Settings, addresses: str
) -> None:
    store = _with_overlay(settings)

    assert store.adopt_reported_address("mpp-a", "10.99.0.2", _info(addresses)) is None
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.29"


def test_a_helper_too_old_to_report_addresses_changes_nothing(
    settings: Settings,
) -> None:
    store = _with_overlay(settings)

    old_helper = reported_addresses(
        parse_response("OK probe-info\n", expected=HelperCommand.PROBE_INFO)
    )
    assert old_helper == ()
    assert store.adopt_reported_address("mpp-a", "10.99.0.2", old_helper) is None


def test_the_address_already_known_is_not_rewritten(
    settings: Settings, tmp_path: Path
) -> None:
    store = _with_overlay(settings)
    path = settings.probe_dir / "mpp-a.env"
    before = path.read_text(encoding="utf-8")

    assert (
        store.adopt_reported_address("mpp-a", "10.99.0.2", _info("198.51.100.29"))
        is None
    )
    assert path.read_text(encoding="utf-8") == before


def test_the_observer_adopts_only_from_probe_info(settings: Settings) -> None:
    """The transport reports every answer; only probe-info carries addresses,
    and an iperf endpoint on the same transport is no probe at all."""
    store = _with_overlay(settings)
    observe = inventory_route_observer(store)
    connection = ProbeConnection.for_probe(store.read_probe("mpp-a"))
    answer = "OK probe-info\naddresses=198.51.100.40\n"

    observe(connection, "10.99.0.2", HelperRequest(HelperCommand.STATUS), answer)
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.29"

    class Endpoint:
        label = "iperf-berlin"
        host = "10.99.0.2"
        port = 22
        fallback_host = None

    observe(Endpoint(), "10.99.0.2", HelperRequest(HelperCommand.PROBE_INFO), answer)
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.29"

    observe(connection, "10.99.0.2", HelperRequest(HelperCommand.PROBE_INFO), answer)
    assert store.read_probe("mpp-a").ssh_host == "198.51.100.40"
