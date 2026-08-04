"""Talk to an iperf3 measurement endpoint over its management channel.

The same transport the probes use - same key, same pinned known_hosts, same
wire format - reaching a different account behind a different forced command.
What differs is the vocabulary: four requests, listed in full below, because an
endpoint measures and is not otherwise managed.

Kept apart from ProbeHelperClient rather than folded into it. The two channels
answer to different hosts with different rights, and a client that could send
``sensor-stage`` to an endpoint would be one autocomplete away from a request
nobody meant to make.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import ProbeProtocolError
from app.core.logging import get_logger
from app.infrastructure.probe_helper.client import HelperTransport, SshHelperTransport
from app.infrastructure.probe_helper.protocol import (
    HelperCommand,
    HelperRequest,
    HelperResponse,
    parse_response,
)

logger = get_logger(__name__)

# The account libexec/iperf-enroll.sh creates. Deliberately not the probes'
# prtg-nats-admin: the two hosts grant different rights, and sharing the name
# would make a misdirected connection look like a working one.
MANAGEMENT_USER = "prtg-nats-iperf"

_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")

# Setting up an endpoint can wait for a package manager that first has to take
# a lock from an unattended-upgrade run. The same generous window the probe
# side gives its own package operations.
SETUP_TIMEOUT_SECONDS = 900


@dataclass(frozen=True, slots=True)
class EndpointConnection:
    """Everything needed to reach one endpoint. Comes from its record."""

    name: str
    host: str
    port: int = 22

    def __post_init__(self) -> None:
        if not _HOST_PATTERN.match(self.host):
            raise ProbeProtocolError(
                params={"probe": self.name},
                details=f"invalid endpoint SSH host: {self.host!r}",
            )

    @property
    def label(self) -> str:
        return self.name


class IperfHelperClient:
    """Typed access to the endpoint helper - one method per request."""

    def __init__(
        self,
        transport: HelperTransport,
        *,
        default_timeout: int = 120,
    ) -> None:
        self._transport = transport
        self._default_timeout = default_timeout

    async def _call(
        self,
        connection: EndpointConnection,
        command: HelperCommand,
        *arguments: str,
        payload: str | None = None,
        timeout: int | None = None,
    ) -> HelperResponse:
        request = HelperRequest(
            command=command, arguments=tuple(arguments), payload=payload
        )
        raw = await self._transport.run(
            connection, request, timeout or self._default_timeout
        )
        logger.debug(
            "iperf helper call",
            extra={"endpoint": connection.name, "helper_command": command.value},
        )
        return parse_response(raw, expected=command)

    async def endpoint_info(self, connection: EndpointConnection) -> HelperResponse:
        """iperf3 version, service state, configured port and user, and the
        address this host sees us arrive from."""
        return await self._call(connection, HelperCommand.ENDPOINT_INFO, timeout=30)

    async def endpoint_setup(
        self,
        connection: EndpointConnection,
        *,
        username: str,
        port: int,
        password: str,
    ) -> HelperResponse:
        """Set the endpoint to the state this platform holds for it.

        Idempotent, and the same request for the first setup and for every
        later password change: afterwards the endpoint authenticates against
        exactly the password sent here, whatever it held before. The key pair
        is left alone - replacing it costs every already-served probe its
        access.

        The password travels as payload, never as an argument: an argument
        would appear in the endpoint's process list.
        """
        return await self._call(
            connection,
            HelperCommand.ENDPOINT_SETUP,
            username,
            str(port),
            payload=password,
            timeout=SETUP_TIMEOUT_SECONDS,
        )

    async def endpoint_remove(self, connection: EndpointConnection) -> HelperResponse:
        """Stop the service and delete the key pair and the credentials.

        The iperf3 package stays installed; something else on that host may be
        using it, and this platform did not put it there in every case.
        """
        return await self._call(connection, HelperCommand.ENDPOINT_REMOVE, timeout=180)

    async def unenroll(self, connection: EndpointConnection) -> HelperResponse:
        """Remove the management access from the endpoint side."""
        return await self._call(connection, HelperCommand.UNENROLL)


def build_client(settings: Settings) -> IperfHelperClient:
    """The real client, over SSH. Same key and same pinned known_hosts as the
    probe channel - only the account on the far side differs."""
    transport = SshHelperTransport(
        key_path=settings.ssh_key_path,
        known_hosts_path=settings.ssh_known_hosts_path,
        connect_timeout=settings.ssh_connect_timeout_seconds,
        username=MANAGEMENT_USER,
    )
    return IperfHelperClient(
        transport, default_timeout=settings.ssh_command_timeout_seconds
    )
