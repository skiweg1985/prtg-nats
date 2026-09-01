"""The platform's own NATS connection, for availability monitoring.

`app/infrastructure/nats.py` reads the server's monitoring endpoint over
HTTP. This is the other thing: a real client on the messaging port, because
the watching sensors talk to the platform over the same server the probes
already use.

It connects as the shared ``prtg-nats`` account, over TLS, against the
loopback address - the API container joins the host network namespace, so
the port published for the probes is reachable at 127.0.0.1 without opening
anything further.

The connection is optional by design. An installation with no watched
devices has nothing to receive, and a NATS server that is down is already
visible everywhere else in the interface; neither is a reason for the API
not to start.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import nats
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.errors import Error as NatsError

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.watch import REPORT_WILDCARD, TARGETS_WILDCARD
from app.infrastructure.runtime_files import RuntimeFileStore

logger = get_logger(__name__)

# The account every installation has. Per-probe accounts exist too, but this
# one is the platform's own and is not handed to a probe.
PLATFORM_ACCOUNT = "prtg-nats"

# Long enough that a NATS restart does not produce a wall of log lines,
# short enough that the first report after one is not lost for minutes.
RECONNECT_SECONDS = 10
MAX_RECONNECTS = -1  # forever: the platform outlives any single NATS restart


@dataclass(frozen=True, slots=True)
class BusCredentials:
    url: str
    user: str
    password: str
    ca_path: str
    # The name on the certificate. The packets go to loopback, the handshake
    # is made against this - see _ssl_context.
    tls_hostname: str


class WatchBusUnavailableError(RuntimeError):
    """No credentials, no certificate, or no server to connect to."""


def read_credentials(settings: Settings, runtime: RuntimeFileStore) -> BusCredentials:
    """Read the platform's own account out of runtime/, where it lives.

    Deliberately no fallback to environment variables: runtime/ is the source
    of truth for credentials (ADR 0002), and a second place to configure this
    is a second place for it to be wrong.
    """
    site = runtime.site_settings()
    credential_path = settings.credential_dir / f"{PLATFORM_ACCOUNT}.env"
    if not credential_path.is_file():
        raise WatchBusUnavailableError(f"no credentials at {credential_path}")

    values: dict[str, str] = {}
    for line in credential_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()

    password = values.get("NATS_PASSWORD", "")
    if not password:
        raise WatchBusUnavailableError(f"{credential_path} holds no password")
    if not site.nats_fqdn:
        raise WatchBusUnavailableError("NATS_FQDN is not configured")

    ca_path = settings.cert_dir / "ca.pem"
    if not ca_path.is_file():
        raise WatchBusUnavailableError(f"no CA at {ca_path}")

    # The certificate names the FQDN, so the TLS handshake has to be made
    # against that name even though the packets go to loopback. See
    # _ssl_context below for how the two are reconciled.
    return BusCredentials(
        url=f"tls://127.0.0.1:{site.nats_port}",
        user=values.get("NATS_USERNAME", PLATFORM_ACCOUNT),
        password=password,
        ca_path=str(ca_path),
        tls_hostname=site.nats_fqdn,
    )


class WatchBus:
    """One NATS connection with the two subscriptions this feature needs."""

    def __init__(self, settings: Settings, runtime: RuntimeFileStore) -> None:
        self._settings = settings
        self._runtime = runtime
        self._client: NatsClient | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(
        self,
        *,
        on_report: Callable[[str, bytes], Awaitable[None]],
        on_targets: Callable[[str, bytes], Awaitable[bytes]],
    ) -> None:
        """Connect and subscribe. Raises WatchBusUnavailableError if it cannot."""
        async with self._lock:
            if self.connected:
                return
            credentials = read_credentials(self._settings, self._runtime)

            async def handle_report(message: Msg) -> None:
                account = _account_of(message.subject)
                if not account:
                    return
                try:
                    await on_report(account, message.data)
                except Exception:
                    # A malformed report from one probe must not take the
                    # subscription down for every other probe.
                    logger.exception(
                        "could not process a report", extra={"probe": account}
                    )

            async def handle_targets(message: Msg) -> None:
                account = _account_of(message.subject)
                if not account or not message.reply:
                    return
                try:
                    answer = await on_targets(account, message.data)
                except Exception:
                    logger.exception(
                        "could not answer a targets request",
                        extra={"probe": account},
                    )
                    return
                await message.respond(answer)

            try:
                self._client = await nats.connect(
                    servers=[credentials.url],
                    user=credentials.user,
                    password=credentials.password,
                    tls=_ssl_context(credentials.ca_path),
                    tls_hostname=credentials.tls_hostname,
                    name="prtg-nats-web-watch",
                    allow_reconnect=True,
                    max_reconnect_attempts=MAX_RECONNECTS,
                    reconnect_time_wait=RECONNECT_SECONDS,
                    error_cb=_log_error,
                    disconnected_cb=_log_disconnected,
                    reconnected_cb=_log_reconnected,
                )
                await self._client.subscribe(REPORT_WILDCARD, cb=handle_report)
                await self._client.subscribe(TARGETS_WILDCARD, cb=handle_targets)
            except (NatsError, OSError, ssl.SSLError) as error:
                self._client = None
                raise WatchBusUnavailableError(str(error)) from error

            logger.info(
                "watching for device reports",
                extra={"url": credentials.url, "account": credentials.user},
            )

    async def close(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.drain()
        except (NatsError, OSError):
            # Draining is a courtesy to messages in flight, not a promise.
            await client.close()


def _ssl_context(ca_path: str) -> ssl.SSLContext:
    """Verify the server certificate against this installation's own CA.

    Hostname checking stays on. The connection goes to 127.0.0.1 while the
    certificate names the installation's FQDN, which is why the client is
    told the hostname separately - verifying against the real name over
    loopback rather than turning verification off.
    """
    context = ssl.create_default_context(cafile=ca_path)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _account_of(subject: str) -> str:
    """The account is the last token of the subject."""
    _, _, account = subject.rpartition(".")
    return account


async def _log_error(error: Exception) -> None:
    logger.warning("NATS connection error", extra={"error": str(error)})


async def _log_disconnected() -> None:
    logger.warning("disconnected from NATS, will retry")


async def _log_reconnected() -> None:
    logger.info("reconnected to NATS")
