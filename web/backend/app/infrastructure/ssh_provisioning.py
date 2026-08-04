"""Reaching a host that does not know us yet.

Every other SSH path in this platform speaks to a host that already carries our
management key. This one is what puts it there, and it exists because the probe
ceremony does not fit an iperf measurement endpoint:

A probe has to reach the NATS server or it is useless, and it often sits behind
NAT where we cannot reach it - so it fetches a bootstrap script and reports in.
An endpoint is the other way round. It never needs to know this platform at
all; it only has to answer the probes. What it does need is to be reachable
from here over SSH, because that is what the management channel is. Asking it
to reach us as well would add a requirement the topology does not have, and on
a measurement endpoint standing on a public address that requirement is
frequently the one thing missing.

So this connects outwards, installs the access, and forgets the credentials it
used. They live in the job's ``secrets`` for the length of the run and are
written down nowhere - which is exactly what that field was built for.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

from app.core.errors import ProbeRejectedError, ProbeUnreachableError
from app.core.logging import get_logger
from app.infrastructure.known_hosts import HostKey

logger = get_logger(__name__)

# One per key type a current sshd offers. Asked for separately because the
# handshake settles on exactly one, and pinning only that one would leave the
# next connection - which may negotiate differently - looking like a mismatch.
SCAN_ALGORITHMS: tuple[tuple[str, ...], ...] = (
    ("ssh-ed25519",),
    ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"),
    ("ecdsa-sha2-nistp256",),
)

# A staging directory under /tmp, matching the shape the shell path used and
# the pattern its cleanup checks against. The noqa is honest: S108 is about
# predictable temporary paths in this process's own filesystem, and this path
# exists on the endpoint - created there by mktemp, which picks the suffix
# atomically, and checked against the pattern before anything is removed.
_STAGE_TEMPLATE = "/tmp/prtg-iperf-enroll.XXXXXX"  # noqa: S108 - remote path
_STAGE_PATTERN = "/tmp/prtg-iperf-enroll."  # noqa: S108 - remote path


@dataclass(frozen=True, slots=True)
class AdminCredentials:
    """A one-time sign-in as an administrator of the target host.

    Never persisted, never logged, and dropped when the job ends. A private key
    is preferable to a password and both are accepted, because whoever runs an
    endpoint on a public address does not always have key access to hand.
    """

    username: str
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    # Answering sudo's prompt. Usually the same as the login password, which is
    # why it defaults to it rather than being asked for twice.
    sudo_password: str | None = None

    @property
    def effective_sudo_password(self) -> str | None:
        return self.sudo_password or self.password

    def client_keys(self) -> list[asyncssh.SSHKey]:
        if not self.private_key:
            return []
        try:
            return [asyncssh.import_private_key(self.private_key, self.key_passphrase)]
        except asyncssh.KeyImportError as exc:
            raise ProbeRejectedError(
                params={"probe": self.username},
                details=f"the private key could not be read: {exc}",
            ) from exc


@dataclass(slots=True)
class ProvisioningLog:
    """What the run did, in the order it did it - for the job log.

    Collected rather than logged directly: this module has no job context, and
    a caller that reports progress needs the steps, not a stream.
    """

    lines: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.lines.append(message)


async def scan_host_keys(
    host: str, port: int = 22, *, timeout: int = 10
) -> tuple[HostKey, ...]:
    """The host's SSH keys, without signing in - what ssh-keyscan does.

    Deliberately its own step. The keys have to be seen and accepted by a
    person before any credential travels to that address, and the only honest
    moment for that is before the first sign-in rather than after it.
    """
    found: dict[str, HostKey] = {}
    for algorithms in SCAN_ALGORITHMS:
        try:
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(
                    host, port=port, server_host_key_algs=list(algorithms)
                ),
                timeout=timeout,
            )
        except (OSError, asyncssh.Error, TimeoutError):
            # A host that does not offer this type is the ordinary case, not a
            # failure. Only an empty result at the end is one.
            continue
        if key is None:
            continue
        parsed = HostKey.parse(key.export_public_key("openssh").decode("utf-8"))
        if parsed is not None:
            found[parsed.blob] = parsed

    if not found:
        raise ProbeUnreachableError(
            params={"probe": host, "host": host},
            details=f"no SSH host key could be read from {host}:{port}",
        )
    return tuple(found.values())


async def install_access(
    *,
    host: str,
    port: int,
    credentials: AdminCredentials,
    known_hosts_path: Path,
    files: dict[str, str],
    command: list[str],
    timeout: int = 300,
) -> ProvisioningLog:
    """Sign in as an administrator, put the files there, run one command.

    The host keys are already pinned by the time this runs, and this connection
    verifies against that same file - so the sign-in that carries a password
    goes to the host somebody looked at, not to whatever answered the address.

    Files travel over the open session as payload rather than as arguments, and
    the staging directory goes again whichever way the run ends. Nothing here
    is written to the host outside that directory; the command is what installs
    anything permanent.
    """
    report = ProvisioningLog()
    try:
        async with asyncssh.connect(
            host,
            port=port,
            username=credentials.username,
            password=credentials.password,
            client_keys=credentials.client_keys(),
            known_hosts=str(known_hosts_path),
            connect_timeout=15,
        ) as connection:
            stage = await _make_stage(connection, host)
            report.add(f"staged in {stage}")
            try:
                for name, content in files.items():
                    await _write_file(connection, stage, name, content, host)
                    report.add(f"transferred {name}")
                output = await asyncio.wait_for(
                    _run_privileged(connection, stage, command, credentials, host),
                    timeout=timeout,
                )
                report.add(output.strip() or "the enrolment script said nothing")
            finally:
                # The management public key is in there, which is not a secret,
                # but a staging directory left behind on somebody's machine is
                # still litter we made.
                await _remove_stage(connection, stage)
    except asyncssh.HostKeyNotVerifiable as exc:
        raise ProbeUnreachableError(
            params={"probe": host, "host": host},
            details=f"host key is not pinned in known_hosts: {exc}",
        ) from exc
    except asyncssh.PermissionDenied as exc:
        raise ProbeRejectedError(
            params={"probe": host, "command": "sign-in"},
            details=(f"{credentials.username}@{host} refused the credentials: {exc}"),
        ) from exc
    except (OSError, asyncssh.Error) as exc:
        raise ProbeUnreachableError(
            params={"probe": host, "host": host}, details=str(exc)
        ) from exc
    except TimeoutError as exc:
        raise ProbeUnreachableError(
            params={"probe": host, "timeout": timeout},
            details="the host did not finish the enrolment within the timeout",
        ) from exc
    return report


async def _make_stage(connection: asyncssh.SSHClientConnection, host: str) -> str:
    result = await connection.run(
        f"umask 077; mktemp -d {_STAGE_TEMPLATE}", check=False
    )
    stage = str(result.stdout or "").strip()
    if result.exit_status != 0 or not stage.startswith(_STAGE_PATTERN):
        raise ProbeRejectedError(
            params={"probe": host, "command": "mktemp"},
            details=f"unexpected staging path: {stage[:120]!r}",
        )
    return stage


async def _write_file(
    connection: asyncssh.SSHClientConnection,
    stage: str,
    name: str,
    content: str,
    host: str,
) -> None:
    # The name is ours, not the caller's, but quoting it costs nothing and the
    # day somebody passes one through is the day it matters.
    target = f"{stage}/{name}"
    result = await connection.run(
        f"umask 077; cat > {_quote(target)}", input=content, check=False
    )
    if result.exit_status != 0:
        raise ProbeRejectedError(
            params={"probe": host, "command": "write"},
            details=f"could not write {name}: {str(result.stderr or '')[:200]}",
        )


async def _run_privileged(
    connection: asyncssh.SSHClientConnection,
    stage: str,
    command: list[str],
    credentials: AdminCredentials,
    host: str,
) -> str:
    """Run the enrolment script as root, however this host gets there.

    Three cases, and they are all the ordinary ones: already root, sudo without
    a password, sudo with one. The password goes to sudo's stdin with an empty
    prompt, never on the command line.
    """
    quoted = " ".join(_quote(part.replace("@@STAGE@@", stage)) for part in command)
    sudo_password = credentials.effective_sudo_password

    if credentials.username == "root":
        invocation = quoted
        stdin: str | None = None
    elif sudo_password:
        invocation = f"sudo -S -p '' -- {quoted}"
        stdin = f"{sudo_password}\n"
    else:
        invocation = f"sudo -n -- {quoted}"
        stdin = None

    result = await connection.run(invocation, input=stdin, check=False)
    if result.exit_status != 0:
        detail = (str(result.stderr or "") or str(result.stdout or "")).strip()
        raise ProbeRejectedError(
            params={"probe": host, "command": "iperf-enroll"},
            details=detail[:2000] or "the enrolment script failed without output",
        )
    return str(result.stdout or "")


async def _remove_stage(connection: asyncssh.SSHClientConnection, stage: str) -> None:
    if not stage.startswith(_STAGE_PATTERN):
        return
    try:
        await connection.run(f"rm -rf -- {_quote(stage)}", check=False)
    except (OSError, asyncssh.Error):
        # The run is over either way, and a staging directory that survives is
        # worth neither a failure nor a second message.
        logger.debug("could not remove staging directory", extra={"stage": stage})


def _quote(value: str) -> str:
    """Single-quote for a remote shell, the way shlex would locally."""
    return "'" + value.replace("'", "'\\''") + "'"
