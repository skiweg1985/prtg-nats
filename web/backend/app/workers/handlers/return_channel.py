"""The client certificate a probe reports back with (ADR 0013).

One job step shared by enrolment, the helper update and the repair job, so a
probe ends up with the same certificate whichever of them got there first.
The key is made on the probe and stays there; only the CSR and the signed
certificate cross the management channel.
"""

from __future__ import annotations

from typing import Any, Protocol

from cryptography import x509

from app.core.config import Settings
from app.infrastructure.pki import Pki
from app.infrastructure.probe_helper import (
    HelperResponse,
    ProbeConnection,
    ProbeHelperClient,
)
from app.infrastructure.probe_helper.protocol import (
    RETURN_CHANNEL_HELPER_VERSION,
    ChannelSettings,
    normalise_optional,
)
from app.infrastructure.runtime_files import RuntimeFileStore

STEP = "install_return_channel"


class ChannelContext(Protocol):
    """What the step needs from its caller.

    A job context satisfies it, and so does the recovery command line, which
    runs the same step for the shell enrolment and helper update without a
    job behind it - one implementation of how a probe gets its certificate.
    """

    @property
    def settings(self) -> Settings: ...

    @property
    def runtime(self) -> RuntimeFileStore: ...

    @property
    def helper(self) -> ProbeHelperClient: ...

    async def log(
        self,
        code: str,
        *,
        params: dict[str, Any] | None = None,
        target: str | None = None,
    ) -> None: ...


def channel_settings(context: ChannelContext, username: str) -> ChannelSettings:
    """What the probe's channel.env should say, from the settings as they are.

    Built for every install and every service refresh, so a changed buffer
    size or interface address reaches a probe with its next one.
    """
    inventory = context.runtime.read_probe(username)
    site = context.runtime.site_settings()
    return ChannelSettings(
        platform_url=site.probe_channel_url(inventory.nats_host_override),
        web_url=site.web_url,
        buffer_bytes=context.settings.probe_channel_buffer_mb * 1024 * 1024,
    )


async def refresh_service(
    context: ChannelContext, username: str, connection: ProbeConnection
) -> HelperResponse:
    """Rewrite and restart the probe's service, its certificate untouched."""
    return await context.helper.channel_service(
        connection, channel_settings(context, username)
    )


async def install_return_channel(
    context: ChannelContext, username: str, connection: ProbeConnection
) -> str:
    """Issue a certificate for the probe and install it. Returns its expiry.

    Runs after the inventory is written: the identity the certificate names
    is the probe's NATS username, and the CA refuses to sign for one the
    inventory does not know.
    """
    pki = Pki(context.settings)
    csr_pem = await context.helper.channel_csr(connection)
    certificate_pem = pki.sign_probe_csr(csr_pem, username)

    settings = channel_settings(context, username)
    url = settings.platform_url
    await context.helper.channel_install(
        connection,
        certificate_pem=certificate_pem,
        client_ca_pem=pki.probe_ca_pem(),
        settings=settings,
    )
    expires = (
        x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
        .not_valid_after_utc.date()
        .isoformat()
    )
    await context.log(
        "jobs.probe.return_channel_installed",
        params={"probe": username, "url": url, "expires": expires},
        target=username,
    )
    return expires


async def install_if_missing(
    context: ChannelContext,
    username: str,
    connection: ProbeConnection,
    info: HelperResponse,
) -> str | None:
    """Give a probe a certificate unless it has one. Returns its expiry.

    For the helper update, which every probe enrolled before ADR 0013 goes
    through anyway. A probe that already has a certificate keeps it - renewal
    belongs to the channel itself, and replacing a working certificate as a
    side effect of an update would only add a way for the update to break
    it; only its service is refreshed. A helper too old to make a key is
    noted and left alone: the update itself succeeded, and the next one will
    catch up.
    """
    reported = info.value("helper_version") or ""
    if not reported.isdigit() or int(reported) < RETURN_CHANNEL_HELPER_VERSION:
        await context.log(
            "jobs.probe.return_channel_helper_outdated",
            params={
                "probe": username,
                "version": reported or "unknown",
                "required": str(RETURN_CHANNEL_HELPER_VERSION),
            },
            target=username,
        )
        return None

    present = normalise_optional(info.value("channel_cert"))
    if present:
        await context.log(
            "jobs.probe.return_channel_present",
            params={"probe": username, "expires": present},
            target=username,
        )
        # The service is written by the helper, so the helper that was just
        # installed brings its own version of it. Rewritten and restarted
        # here; the certificate stays as it is.
        await refresh_service(context, username, connection)
        return present

    return await install_return_channel(context, username, connection)
