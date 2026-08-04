"""Sensor maintenance on one probe: removal, endpoint profiles and variants."""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError, NotFoundError
from app.domain.enums import LogLevel
from app.infrastructure.probe_helper import ProbeConnection
from app.workers.context import JobContext
from app.workers.handlers.deploy_sensor import (
    _deploy_endpoint_profiles,
    deploy_variant,
)

REMOVE_STEPS: tuple[str, ...] = ("check_reachable", "remove", "bookkeeping")
REMOVE_JOB_TYPE = "sensor.remove"

PROFILES_STEPS: tuple[str, ...] = ("check_reachable", "deploy_profiles")
PROFILES_JOB_TYPE = "sensor.deploy_profiles"

WRITE_PROFILE_STEPS: tuple[str, ...] = ("resolve_targets", "deploy")
WRITE_PROFILE_JOB_TYPE = "sensor.write_profile"

REMOVE_PROFILE_STEPS: tuple[str, ...] = ("resolve_targets", "remove")
REMOVE_PROFILE_JOB_TYPE = "sensor.remove_profile"


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


async def write_profile(context: JobContext) -> dict[str, Any]:
    """Put one variant onto the probes it is assigned to.

    The payload names the variant, never its values: they were written to
    runtime/sensor-profiles/ by the request that created this job, and are read
    from there. That way no credential is ever stored in the job table, and a
    retry deploys the state as it stands rather than a stale copy.
    """
    sensor: str = context.payload["sensor"]
    profile: str = context.payload["profile"]
    probes: list[str] = list(context.payload["probes"])

    await context.step("resolve_targets")
    definition = context.catalog.get(sensor)
    await context.log(
        "jobs.sensor.profile_resolved",
        params={"sensor": sensor, "profile": profile, "probes": len(probes)},
    )

    await context.step("deploy")
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for username in probes:
        try:
            connection = _connection(context, username)
            await deploy_variant(context, connection, definition, username, profile)
            succeeded.append(username)
        except AppError as error:
            failed.append({"probe": username, "code": error.code})
            await context.log(
                "jobs.sensor.profile_failed",
                level=LogLevel.ERROR,
                params={"probe": username, "profile": profile, "reason": error.code},
                target=username,
                raw=error.details,
            )
    return {
        "sensor": sensor,
        "profile": profile,
        "succeeded": succeeded,
        "failed": failed,
    }


async def remove_profile(context: JobContext) -> dict[str, Any]:
    """Take one variant off the probes that hold it.

    Best effort per probe: a variant that cannot be removed from an unreachable
    probe must not stop it being removed from the reachable ones, or a rotated
    credential would stay in place wherever the first failure happened.
    """
    sensor: str = context.payload["sensor"]
    profile: str = context.payload["profile"]
    probes: list[str] = list(context.payload["probes"])

    await context.step("resolve_targets")
    await context.log(
        "jobs.sensor.profile_resolved",
        params={"sensor": sensor, "profile": profile, "probes": len(probes)},
    )

    await context.step("remove")
    succeeded: list[str] = []
    failed: list[dict[str, str]] = []
    for username in probes:
        try:
            connection = _connection(context, username)
            await context.helper.remove_profile(connection, sensor, profile)
            await context.helper.remove_profile_files(connection, sensor, profile)
            context.runtime.unassign_profile(username, sensor, profile)
            succeeded.append(username)
            await context.log(
                "jobs.sensor.profile_removed",
                params={"probe": username, "sensor": sensor, "profile": profile},
                target=username,
            )
        except AppError as error:
            failed.append({"probe": username, "code": error.code})
            await context.log(
                "jobs.sensor.profile_failed",
                level=LogLevel.ERROR,
                params={"probe": username, "profile": profile, "reason": error.code},
                target=username,
                raw=error.details,
            )
    return {
        "sensor": sensor,
        "profile": profile,
        "succeeded": succeeded,
        "failed": failed,
    }
