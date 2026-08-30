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
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import asyncssh

from app.core.errors import (
    ProbeHelperOutdatedError,
    ProbeProtocolError,
    ProbeRejectedError,
    ProbeUnreachableError,
    RuntimeStateError,
)
from app.core.logging import get_logger
from app.infrastructure.probe_helper.protocol import (
    UNSUPPORTED_REQUEST_MESSAGE,
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
_ACTIVE_TRANSACTION_PATTERN = re.compile(
    r"^active_transaction=([A-Za-z0-9][A-Za-z0-9._-]{0,63})$", re.MULTILINE
)


def refusal_error(
    probe: str, command: HelperCommand, output: str
) -> ProbeRejectedError | ProbeHelperOutdatedError:
    """Which error a non-zero exit from the helper deserves.

    Its own refusal messages are precise, so they are kept verbatim in
    ``details``, where nothing translates them. One of them is not a rejection
    at all: a helper too old to know the request says so in the same shape, and
    the way out of it is to renew the helper rather than to fix the request.
    """
    reason = output.strip()[:2000]
    params = {"probe": probe, "command": command.value}
    active_transaction = _ACTIVE_TRANSACTION_PATTERN.search(reason)
    if active_transaction is not None:
        params["active_transaction"] = active_transaction.group(1)
    if UNSUPPORTED_REQUEST_MESSAGE in reason:
        return ProbeHelperOutdatedError(params=params, details=reason)
    return ProbeRejectedError(params=params, details=reason)


class HelperTarget(Protocol):
    """What the transport needs to dial one host.

    A protocol rather than the dataclass below, because the iperf endpoints
    reach their own helper over the same transport with a different account -
    same key, same pinned known_hosts, different forced command.
    """

    @property
    def label(self) -> str:
        """What this host is called in errors and logs."""
        ...

    @property
    def host(self) -> str: ...

    @property
    def port(self) -> int: ...


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

    @property
    def label(self) -> str:
        return self.nats_username


class HelperTransport(Protocol):
    """Seam for tests: the whole SSH layer behind one method."""

    async def run(
        self, connection: HelperTarget, request: HelperRequest, timeout: int
    ) -> str: ...


class SshHelperTransport:
    def __init__(
        self,
        *,
        key_path: Path,
        known_hosts_path: Path,
        connect_timeout: int = 10,
        username: str = MANAGEMENT_USER,
    ) -> None:
        self._key_path = key_path
        self._known_hosts_path = known_hosts_path
        self._connect_timeout = connect_timeout
        self._username = username

    async def run(
        self, connection: HelperTarget, request: HelperRequest, timeout: int
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
                username=self._username,
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
                params={"probe": connection.label, "host": connection.host},
                details=f"host key is not pinned in known_hosts: {exc}",
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ProbeUnreachableError.of(connection.label, details=str(exc)) from exc
        except TimeoutError as exc:
            raise ProbeUnreachableError(
                params={"probe": connection.label, "timeout": timeout},
                details="the host did not answer within the timeout",
            ) from exc

        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.exit_status != 0:
            raise refusal_error(connection.label, request.command, (stderr or stdout))
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

    async def wireless_interfaces(self, connection: ProbeConnection) -> HelperResponse:
        """The probe's radio interfaces, with what a reservation would cost."""
        return await self._call(
            connection, HelperCommand.WIRELESS_INTERFACES, timeout=30
        )

    async def is_reachable(self, connection: ProbeConnection) -> bool:
        try:
            await self.probe_info(connection)
        except (
            ProbeUnreachableError,
            ProbeRejectedError,
            ProbeProtocolError,
            ProbeHelperOutdatedError,
        ):
            return False
        return True

    # --- Certificate --------------------------------------------------------

    async def install_ca(
        self, connection: ProbeConnection, ca_pem: str
    ) -> HelperResponse:
        return await self._call(
            connection, HelperCommand.INSTALL_CA, payload=ca_pem, timeout=60
        )

    # --- The helper itself --------------------------------------------------

    async def helper_update(
        self, connection: ProbeConnection, script: str, signature: str
    ) -> HelperResponse:
        """Replace the helper on the probe with the one this platform ships.

        The signature travels as the argument and the script as the payload,
        because the probe checks the one against the other before it writes
        anything. The management key opens this channel, but replacing this
        root helper additionally requires the release signature.
        """
        return await self._call(
            connection,
            HelperCommand.HELPER_UPDATE,
            signature,
            payload=script,
            timeout=60,
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

    async def sensor_tool_stage(
        self,
        connection: ProbeConnection,
        transaction: str,
        sensor: str,
        envelope: str,
        signature: str,
    ) -> HelperResponse:
        """Stage one release-signed executable inside the sensor transaction."""
        return await self._call(
            connection,
            HelperCommand.SENSOR_TOOL_STAGE,
            transaction,
            sensor,
            signature,
            payload=envelope,
            timeout=180,
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

    async def write_profile_file(
        self,
        connection: ProbeConnection,
        sensor: str,
        profile: str,
        filename: str,
        payload: bytes,
    ) -> HelperResponse:
        """Place a certificate or key belonging to a variant.

        Base64 because the channel carries text and a key is bytes; the profile
        itself is KEY=VALUE lines and needs no such wrapping. The helper builds
        the destination path from its own validated tokens, so only the file
        name travels, never a path.
        """
        return await self._call(
            connection,
            HelperCommand.SENSOR_WRITE_FILE,
            sensor,
            profile,
            filename,
            payload=base64.b64encode(payload).decode("ascii"),
        )

    async def remove_profile_files(
        self, connection: ProbeConnection, sensor: str, profile: str
    ) -> HelperResponse:
        """Take every file of one variant off the probe."""
        return await self._call(
            connection, HelperCommand.SENSOR_REMOVE_FILE, sensor, profile
        )

    # --- Retirement ---------------------------------------------------------

    async def mpp_uninstall(self, connection: ProbeConnection) -> HelperResponse:
        """Remove the probe software, its configuration and the package source.

        The timeout is the generous one: this waits for a package manager that
        may first have to take a lock from an unattended-upgrade run.
        """
        return await self._call(connection, HelperCommand.MPP_UNINSTALL, timeout=900)

    async def unenroll(self, connection: ProbeConnection) -> HelperResponse:
        """Remove the management access from the probe side."""
        return await self._call(connection, HelperCommand.UNENROLL)
