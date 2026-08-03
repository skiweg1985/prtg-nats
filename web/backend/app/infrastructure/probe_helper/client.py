"""Talk to the probe management channel over SSH.

The shell tooling opens one ``ssh`` process per request
(``managed_ssh`` in libexec/common.sh). We do the same thing from Python with
asyncssh, using the same key and the same pinned known_hosts file, so both
tools stay interchangeable and the probe side needs no change at all.

Why not reuse the shell for this: the helper protocol is already structured,
and a subprocess per request would cost a fork, lose typed errors and make
every failure a string to grep. Speaking it directly is less code, not more.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import asyncssh

from app.core.errors import (
    ProbeProtocolError,
    ProbeRejectedError,
    ProbeUnreachableError,
    RuntimeStateError,
)
from app.core.logging import get_logger
from app.infrastructure.probe_helper.protocol import (
    HelperCommand,
    HelperRequest,
    HelperResponse,
    parse_response,
)

logger = get_logger(__name__)

# The account the forced command lives under, fixed by enroll_probe().
MANAGEMENT_USER = "prtg-nats-admin"

# Same shape libexec/common.sh validates before it dials.
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")


@dataclass(frozen=True, slots=True)
class ProbeConnection:
    """Everything needed to reach one probe. Comes from its inventory file."""

    nats_username: str
    host: str
    port: int = 22

    def __post_init__(self) -> None:
        if not _HOST_PATTERN.match(self.host):
            raise ProbeProtocolError(
                params={"probe": self.nats_username},
                details=f"invalid enrolled SSH host: {self.host!r}",
            )


class HelperTransport(Protocol):
    """Seam for tests: the whole SSH layer behind one method."""

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str: ...


class SshHelperTransport:
    def __init__(
        self,
        *,
        key_path: Path,
        known_hosts_path: Path,
        connect_timeout: int = 10,
    ) -> None:
        self._key_path = key_path
        self._known_hosts_path = known_hosts_path
        self._connect_timeout = connect_timeout

    async def run(
        self, connection: ProbeConnection, request: HelperRequest, timeout: int
    ) -> str:
        if not self._key_path.is_file():
            raise RuntimeStateError(
                params={"path": str(self._key_path)},
                details="management SSH key not found; run the stack setup first",
            )

        # Host keys stay pinned in the file the shell tooling maintains.
        # Accepting an unknown host here would silently undo the fingerprint
        # confirmation that enrollment insists on.
        try:
            async with asyncssh.connect(
                connection.host,
                port=connection.port,
                username=MANAGEMENT_USER,
                client_keys=[str(self._key_path)],
                known_hosts=str(self._known_hosts_path),
                connect_timeout=self._connect_timeout,
                keepalive_interval=5,
                keepalive_count_max=3,
            ) as ssh:
                # The forced command ignores whatever we would pass as a
                # command line; the request travels on stdin.
                stdin_payload = request.encode()
                if request.payload is not None:
                    stdin_payload += request.payload
                result = await asyncio.wait_for(
                    ssh.run("", input=stdin_payload, check=False),
                    timeout=timeout,
                )
        except asyncssh.HostKeyNotVerifiable as exc:
            raise ProbeUnreachableError(
                params={"probe": connection.nats_username, "host": connection.host},
                details=f"host key is not pinned in known_hosts: {exc}",
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ProbeUnreachableError.of(
                connection.nats_username, details=str(exc)
            ) from exc
        except TimeoutError as exc:
            raise ProbeUnreachableError(
                params={"probe": connection.nats_username, "timeout": timeout},
                details="the probe did not answer within the timeout",
            ) from exc

        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.exit_status != 0:
            # The helper's own refusal messages are precise; keep them verbatim
            # in details, where they are not translated.
            raise ProbeRejectedError(
                params={
                    "probe": connection.nats_username,
                    "command": request.command.value,
                },
                details=(stderr or stdout).strip()[:2000],
            )
        return stdout


class ProbeHelperClient:
    """Typed access to the probe helper.

    One method per protocol request, so a caller never assembles a command
    string and the set of things that can be asked of a probe stays visible in
    one file.
    """

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
        connection: ProbeConnection,
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
            "probe helper call",
            extra={
                "probe": connection.nats_username,
                "helper_command": command.value,
            },
        )
        return parse_response(raw, expected=command)

    # --- Reading ------------------------------------------------------------

    async def probe_info(self, connection: ProbeConnection) -> HelperResponse:
        """Package version, service state, hostname, CA fingerprint, identity."""
        return await self._call(connection, HelperCommand.PROBE_INFO, timeout=30)

    async def status(self, connection: ProbeConnection) -> HelperResponse:
        """Fails unless the MPP service is active - the cheapest liveness check."""
        return await self._call(connection, HelperCommand.STATUS, timeout=30)

    async def sensor_list(self, connection: ProbeConnection) -> HelperResponse:
        """Installed sensors with version, checksum, interfaces and helper state."""
        return await self._call(connection, HelperCommand.SENSOR_LIST, timeout=30)

    async def is_reachable(self, connection: ProbeConnection) -> bool:
        try:
            await self.probe_info(connection)
        except (ProbeUnreachableError, ProbeRejectedError, ProbeProtocolError):
            return False
        return True

    # --- Certificate --------------------------------------------------------

    async def install_ca(
        self, connection: ProbeConnection, ca_pem: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.INSTALL_CA, payload=ca_pem, timeout=60
        )

    # --- Configuration transaction -----------------------------------------
    # write-config -> activate -> commit, with rollback on failure. The
    # transaction id is chosen by the caller and echoed in every step.
    #
    # The helper also offers a credentials-only entry point (STAGE) that
    # rewrites the active configuration in place. It opens the same
    # transaction write-config would open, so the two are alternatives, not a
    # sequence. This platform always renders the full configuration centrally
    # - credentials included - and therefore only speaks write-config.

    async def write_config(
        self, connection: ProbeConnection, transaction: str, config: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.WRITE_CONFIG, transaction, payload=config
        )

    async def activate(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.ACTIVATE, transaction, timeout=180
        )

    async def commit(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        return await self._call(connection, HelperCommand.COMMIT, transaction)

    async def rollback(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.ROLLBACK, transaction, timeout=180
        )

    # --- Sensor transaction -------------------------------------------------

    async def sensor_prepare(self, connection: ProbeConnection) -> HelperResponse:
        """Create the directories and the service user a sensor needs."""
        return await self._call(connection, HelperCommand.SENSOR_PREPARE, timeout=300)

    async def sensor_stage(
        self,
        connection: ProbeConnection,
        transaction: str,
        sensor: str,
        slot: str,
        content: str,
    ) -> HelperResponse:
        """Upload one file of a sensor: script, wrapper, requirements or version."""
        return await self._call(
            connection,
            HelperCommand.SENSOR_STAGE,
            transaction,
            sensor,
            slot,
            payload=content,
        )

    async def sensor_activate(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        """Install, then self-test under the MPP service's own hardening.

        This is the step that can legitimately fail: the probe restores the
        previous state itself before reporting it.
        """
        return await self._call(
            connection, HelperCommand.SENSOR_ACTIVATE, transaction, timeout=600
        )

    async def sensor_commit(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        return await self._call(connection, HelperCommand.SENSOR_COMMIT, transaction)

    async def sensor_rollback(
        self, connection: ProbeConnection, transaction: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.SENSOR_ROLLBACK, transaction, timeout=180
        )

    async def sensor_remove(
        self, connection: ProbeConnection, sensor: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.SENSOR_REMOVE, sensor, timeout=180
        )

    # --- Sensor accessories -------------------------------------------------

    async def reserve_interface(
        self, connection: ProbeConnection, sensor: str, interface: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.SENSOR_RESERVE_INTERFACE, sensor, interface
        )

    async def release_interface(
        self, connection: ProbeConnection, sensor: str, interface: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.SENSOR_RELEASE_INTERFACE, sensor, interface
        )

    async def write_profile(
        self, connection: ProbeConnection, sensor: str, profile: str, content: str
    ) -> HelperResponse:
        """Place a credential profile. The content never touches a command line."""
        return await self._call(
            connection,
            HelperCommand.SENSOR_WRITE_PROFILE,
            sensor,
            profile,
            payload=content,
        )

    async def remove_profile(
        self, connection: ProbeConnection, sensor: str, profile: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.SENSOR_REMOVE_PROFILE, sensor, profile
        )

    async def unenroll(self, connection: ProbeConnection) -> HelperResponse:
        """Remove the management access from the probe side."""
        return await self._call(connection, HelperCommand.UNENROLL)
