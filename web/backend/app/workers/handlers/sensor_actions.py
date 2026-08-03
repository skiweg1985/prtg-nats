"""Sensor maintenance on one probe: removal and endpoint profiles."""

from __future__ import annotations

from typing import Any

from app.core.errors import NotFoundError
from app.infrastructure.probe_helper import ProbeConnection
from app.workers.context import JobContext
from app.workers.handlers.deploy_sensor import _deploy_endpoint_profiles

REMOVE_STEPS: tuple[str, ...] = ("check_reachable", "remove", "bookkeeping")
REMOVE_JOB_TYPE = "sensor.remove"

PROFILES_STEPS: tuple[str, ...] = ("check_reachable", "deploy_profiles")
PROFILES_JOB_TYPE = "sensor.deploy_profiles"


def _connection(context: JobContext, username: str) -> ProbeConnection:
    inventory = context.runtime.read_probe(username)
    return ProbeConnection(
        nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
    )


def forget_sensor(context: JobContext, username: str, sensor: str) -> None:
    """Correct the local bookkeeping after a sensor left a probe.

    Shared with the retirement path in probe_lifecycle, so a sensor removed
    during a cleanup leaves the same state behind as one removed on its own.
    """
    context.runtime.forget_sensor(username, sensor)
    # The helper clears the endpoint credentials along with the sensor's
    # configuration directory; the bookkeeping here has to follow, or the
    # endpoint list would name probes that hold nothing.
    try:
        definition = context.catalog.get(sensor)
    except NotFoundError:
        # A sensor the probe still carries but the catalogue no longer
        # offers. Removing it has to keep working; only the endpoint
        # bookkeeping is skipped, because no manifest is left to say whether
        # this sensor held any.
        return
    if definition.iperf_kind:
        for endpoint in context.runtime.list_iperf_endpoints():
            context.runtime.forget_iperf(username, endpoint.name)


async def remove_from_probe(
    context: JobContext, connection: ProbeConnection, username: str, sensor: str
) -> None:
    """Take one sensor off a probe, bookkeeping included."""
    await context.helper.sensor_remove(connection, sensor)
    await context.log(
        "jobs.sensor.removed", params={"probe": username, "sensor": sensor}
    )
    forget_sensor(context, username, sensor)


async def remove(context: JobContext) -> dict[str, Any]:
    username: str = context.payload["probe"]
    sensor: str = context.payload["sensor"]
    connection = _connection(context, username)

    await context.step("check_reachable")
    await context.helper.probe_info(connection)

    await context.step("remove")
    await context.helper.sensor_remove(connection, sensor)
    await context.log(
        "jobs.sensor.removed", params={"probe": username, "sensor": sensor}
    )

    await context.step("bookkeeping")
    forget_sensor(context, username, sensor)
    return {"probe": username, "sensor": sensor}


async def deploy_profiles(context: JobContext) -> dict[str, Any]:
    """Hand the credentials of every managed endpoint to one probe.

    Used after a new endpoint is registered, so probes that already run the
    sensor learn about it without a full redeploy.
    """
    username: str = context.payload["probe"]
    sensor: str = context.payload["sensor"]
    connection = _connection(context, username)

    await context.step("check_reachable")
    await context.helper.probe_info(connection)

    await context.step("deploy_profiles")
    definition = context.catalog.get(sensor)
    await _deploy_endpoint_profiles(context, connection, definition, username)
    return {"probe": username, "sensor": sensor}
