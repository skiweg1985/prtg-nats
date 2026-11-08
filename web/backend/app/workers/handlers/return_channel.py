"""The client certificate a probe reports back with (ADR 0013).

One job step shared by enrolment, the helper update and the repair job, so a
probe ends up with the same certificate whichever of them got there first.
The key is made on the probe and stays there; only the CSR and the signed
certificate cross the management channel.
"""

from __future__ import annotations

from cryptography import x509

from app.infrastructure.pki import Pki
from app.infrastructure.probe_helper import HelperResponse, ProbeConnection
from app.infrastructure.probe_helper.protocol import (
    RETURN_CHANNEL_HELPER_VERSION,
    normalise_optional,
)
from app.workers.context import JobContext

STEP = "install_return_channel"


async def install_return_channel(
    context: JobContext, username: str, connection: ProbeConnection
) -> str:
    """Issue a certificate for the probe and install it. Returns its expiry.

    Runs after the inventory is written: the identity the certificate names
    is the probe's NATS username, and the CA refuses to sign for one the
    inventory does not know.
    """
    pki = Pki(context.settings)
    csr_pem = await context.helper.channel_csr(connection)
    certificate_pem = pki.sign_probe_csr(csr_pem, username)

    inventory = context.runtime.read_probe(username)
    url = context.runtime.site_settings().probe_channel_url(
        inventory.nats_host_override
    )
    await context.helper.channel_install(
        connection,
        certificate_pem=certificate_pem,
        client_ca_pem=pki.probe_ca_pem(),
        platform_url=url,
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
    context: JobContext,
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
        await context.helper.channel_service(connection)
        return present

    return await install_return_channel(context, username, connection)
