"""How a moved probe's address reaches the inventory - ADR 0011.

The transport knows which route answered, the inventory knows what the routes
are. This is the one place the two meet, so the transport stays free of
inventories and every place that builds one reaches probes the same way.
"""

from __future__ import annotations

from app.core.config import Settings
from app.infrastructure.probe_helper.client import (
    AnswerObserver,
    HelperTarget,
    SshHelperTransport,
)
from app.infrastructure.probe_helper.protocol import (
    HelperCommand,
    HelperRequest,
    parse_response,
    reported_addresses,
)
from app.infrastructure.runtime_files import RuntimeFileStore


def inventory_route_observer(store: RuntimeFileStore) -> AnswerObserver:
    """Adopts the site address a probe reports when it answered over the
    overlay. probe-info is the request that carries the addresses; anything
    else, and any target that is not a probe, passes through untouched."""

    def observe(
        connection: HelperTarget, answered: str, request: HelperRequest, output: str
    ) -> None:
        if request.command is not HelperCommand.PROBE_INFO:
            return
        username = getattr(connection, "nats_username", None)
        if not username:
            return
        response = parse_response(output, expected=HelperCommand.PROBE_INFO)
        store.adopt_reported_address(username, answered, reported_addresses(response))

    return observe


def management_transport(settings: Settings) -> SshHelperTransport:
    """The transport every probe call goes through, with the observer wired."""
    return SshHelperTransport(
        key_path=settings.ssh_key_path,
        known_hosts_path=settings.ssh_known_hosts_path,
        connect_timeout=settings.ssh_connect_timeout_seconds,
        on_answer=inventory_route_observer(RuntimeFileStore(settings)),
    )
