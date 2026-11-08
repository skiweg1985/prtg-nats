"""Repairing a probe's return channel over the management access (#176).

Started by the inventory sync when a probe that has delivered before falls
silent, or from the probe's page. The platform cannot see why a channel is
quiet - a rejected certificate never reaches the backend, the proxy refuses
it first - so the job decides from what the probe says about itself: a
missing or expired certificate is issued anew, a stopped service is
reinstalled, and a service that runs while the probe stays quiet points at
the certificate as well, so that case gets a new one too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.enums import LogLevel
from app.infrastructure.probe_helper import ProbeConnection
from app.infrastructure.probe_helper.protocol import normalise_optional
from app.services.return_channel import certificate_usable
from app.workers.context import JobContext
from app.workers.handlers import return_channel
from app.workers.handlers.fanout import over_targets

JOB_TYPE = "probe.channel_repair"
STEPS: tuple[str, ...] = ("check_reachable", return_channel.STEP, "verify")


async def repair(context: JobContext) -> dict[str, Any]:
    return await over_targets(context, repair_on)


async def repair_on(context: JobContext, username: str) -> dict[str, Any]:
    connection = ProbeConnection.for_probe(context.runtime.read_probe(username))

    await context.step("check_reachable")
    info = await context.helper.probe_info(connection)
    certificate = normalise_optional(info.value("channel_cert"))
    service = normalise_optional(info.value("channel_service"))
    await context.log(
        "jobs.probe.channel_found",
        params={
            "probe": username,
            "certificate": certificate or "none",
            "service": service or "absent",
        },
        target=username,
    )

    await context.step(return_channel.STEP)
    today = datetime.now(UTC).date()
    if not certificate_usable(certificate, today) or service == "active":
        # channel-install writes and restarts the service as well, so a new
        # certificate repairs both halves in one request.
        certificate = await return_channel.install_return_channel(
            context, username, connection
        )
    else:
        await context.helper.channel_service(connection)
        await context.log(
            "jobs.probe.channel_service_reinstalled",
            params={"probe": username},
            target=username,
        )

    await context.step("verify")
    after = await context.helper.probe_info(connection)
    service = normalise_optional(after.value("channel_service")) or "absent"
    if service == "active":
        await context.log(
            "jobs.probe.channel_repaired", params={"probe": username}, target=username
        )
    else:
        await context.log(
            "jobs.probe.channel_service_not_running",
            level=LogLevel.WARNING,
            params={"probe": username, "service": service},
            target=username,
        )
    return {"probe": username, "channel_cert": certificate, "channel_service": service}
