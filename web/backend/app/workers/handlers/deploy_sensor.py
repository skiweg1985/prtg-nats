"""Roll a sensor out to one or more probes.

The probe side already implements the hard part: staged files, activation with
a self-test under the real service hardening, and an automatic restore if the
test fails. This handler drives that transaction and reports each step, so the
operator sees where a rollout stands instead of a wall of SSH output.

Per probe:

    check_reachable -> prepare -> stage(script, wrapper, requirements, version)
    -> activate -> commit         (rollback on any failure after staging)

Probes are handled one after another on purpose. A parallel rollout that half
succeeds is harder to reason about than a sequential one, and the slow part is
the self-test on the probe, not our side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.core.ids import new_id
from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.infrastructure.probe_helper import SENSOR_SLOTS, ProbeConnection
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorDefinition
from app.persistence.models.inventory import Deployment, DeploymentTarget
from app.workers.context import JobContext

STEPS: tuple[str, ...] = (
    "resolve_targets",
    "check_reachable",
    "prepare",
    "stage_files",
    "activate",
    "commit",
    "verify",
)

JOB_TYPE = "sensor.deploy"


async def run(context: JobContext) -> dict[str, Any]:
    sensor_name: str = context.payload["sensor"]
    probe_usernames: list[str] = list(context.payload["probes"])
    dry_run: bool = bool(context.payload.get("dry_run"))
    deployment_id: str | None = context.payload.get("deployment_id")

    await context.step("resolve_targets")
    definition = context.catalog.get(sensor_name)
    await context.log(
        "jobs.sensor.resolved",
        params={
            "sensor": definition.name,
            "version": definition.version,
            "probes": len(probe_usernames),
        },
    )

    succeeded: list[str] = []
    failed: list[dict[str, str]] = []

    for username in probe_usernames:
        if context.cancelled:
            await context.log("jobs.cancelled", level=LogLevel.WARNING)
            break
        outcome = await deploy_one(context, definition, username, dry_run=dry_run)
        if outcome is None:
            succeeded.append(username)
        else:
            failed.append({"probe": username, **outcome})
        if deployment_id:
            await _record_target(context, deployment_id, username, outcome)

    await context.jobs.finish_step(
        context.job,
        "verify",
        JobStepStatus.SUCCEEDED if not failed else JobStepStatus.FAILED,
    )

    if deployment_id:
        await _finalise_deployment(context, deployment_id, succeeded, failed)

    return {
        "sensor": definition.name,
        "version": definition.version,
        "succeeded": succeeded,
        "failed": failed,
        "dry_run": dry_run,
    }


async def deploy_one(
    context: JobContext,
    definition: SensorDefinition,
    username: str,
    *,
    dry_run: bool,
) -> dict[str, str] | None:
    """Deploy one sensor to one probe. Returns None on success, or the error
    to report for this probe."""
    transaction = new_id()

    try:
        inventory = context.runtime.read_probe(username)
        connection = ProbeConnection(
            nats_username=username,
            host=inventory.ssh_host,
            port=inventory.ssh_port,
        )

        await context.step("check_reachable")
        info = await context.helper.probe_info(connection)
        await context.log(
            "jobs.probe.reachable",
            params={
                "probe": username,
                "package": info.value("package") or "unknown",
            },
            target=username,
        )

        if dry_run:
            await context.log(
                "jobs.sensor.dry_run",
                params={
                    "probe": username,
                    "sensor": definition.name,
                    "version": definition.version,
                    "slots": ", ".join(_slots_for(definition)),
                },
                target=username,
            )
            return None

        await context.step("prepare")
        await context.helper.sensor_prepare(connection)
        await context.log(
            "jobs.probe.prepared", params={"probe": username}, target=username
        )

        await context.step("stage_files")
        for slot in _slots_for(definition):
            content = context.catalog.read_slot(definition, slot)
            await context.helper.sensor_stage(
                connection, transaction, definition.name, slot, content
            )
            await context.log(
                "jobs.sensor.staged",
                params={"probe": username, "slot": slot, "bytes": len(content)},
                target=username,
            )

        await context.step("activate")
        # The probe installs, then runs the sensor under the MPP service's own
        # hardening and checks it produces valid Script v2 JSON. A failure here
        # is the probe restoring the previous state, not us.
        response = await context.helper.sensor_activate(connection, transaction)
        await context.log(
            "jobs.sensor.activated",
            params={"probe": username, "sensor": definition.name},
            target=username,
            raw=response.raw.strip() or None,
        )

        await context.step("commit")
        await context.helper.sensor_commit(connection, transaction)
        # The same bookkeeping the retired CLI kept: the assignment feeds the
        # desired-state fallback and the "which probes run it" views.
        context.runtime.remember_sensor(username, definition.name)
        await context.log(
            "jobs.sensor.committed",
            params={
                "probe": username,
                "sensor": definition.name,
                "version": definition.version,
            },
            target=username,
        )

        if definition.iperf_kind:
            # A sensor that measures against a managed endpoint is not finished
            # without that endpoint's credentials - it would only report
            # "credentials-unreadable". Deliberately after the transaction: the
            # self-test proves the sensor can run, not that it may measure, and
            # an endpoint that does not exist yet must not roll the sensor back.
            await _deploy_endpoint_profiles(context, connection, definition, username)
        return None

    except AppError as error:
        await _try_rollback(context, username, transaction)
        await context.log(
            "jobs.sensor.failed",
            level=LogLevel.ERROR,
            params={
                "probe": username,
                "sensor": definition.name,
                "reason": error.code,
            },
            target=username,
            raw=error.details,
        )
        return {
            "code": error.code,
            "details": error.details or "",
        }
    except Exception as exc:
        await _try_rollback(context, username, transaction)
        await context.log(
            "jobs.sensor.failed",
            level=LogLevel.ERROR,
            params={
                "probe": username,
                "sensor": definition.name,
                "reason": "internal.unexpected",
            },
            target=username,
            raw=f"{type(exc).__name__}: {exc}",
        )
        return {"code": "internal.unexpected", "details": str(exc)}


async def _try_rollback(context: JobContext, username: str, transaction: str) -> None:
    """Best effort. The probe rolls back on its own when activation fails; this
    covers the case where we failed before or after that point."""
    try:
        inventory = context.runtime.read_probe(username)
        connection = ProbeConnection(
            nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
        )
        await context.helper.sensor_rollback(connection, transaction)
        await context.log(
            "jobs.sensor.rolled_back", params={"probe": username}, target=username
        )
    except Exception:
        # An unreachable probe cannot be rolled back from here, and saying so
        # twice adds nothing - the failure above already explains it.
        return


def _slots_for(definition: SensorDefinition) -> tuple[str, ...]:
    available = {file.slot for file in definition.files} | {"version"}
    return tuple(slot for slot in SENSOR_SLOTS if slot in available)


async def _record_target(
    context: JobContext,
    deployment_id: str,
    username: str,
    outcome: dict[str, str] | None,
) -> None:
    target = await context.db.scalar(
        select(DeploymentTarget).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.probe_label == username,
        )
    )
    if target is None:
        return
    target.status = JobStatus.SUCCESSFUL if outcome is None else JobStatus.FAILED
    target.finished_at = datetime.now(UTC)
    if outcome is not None:
        target.error_code = outcome.get("code")
        target.error_details = outcome.get("details")


async def _finalise_deployment(
    context: JobContext,
    deployment_id: str,
    succeeded: list[str],
    failed: list[dict[str, str]],
) -> None:
    deployment = await context.db.get(Deployment, deployment_id)
    if deployment is None:
        return
    if failed and succeeded:
        deployment.status = JobStatus.PARTIALLY_SUCCESSFUL
    elif failed:
        deployment.status = JobStatus.FAILED
    else:
        deployment.status = JobStatus.SUCCESSFUL


def endpoint_profile_content(runtime: RuntimeFileStore, endpoint: str) -> str:
    """The credential profile for one endpoint, as the sensor reads it.

    Shared with the rotation job, which writes the very same file when the
    password changes. Two renderings of one format would be two chances for the
    probes to end up with something the sensor cannot parse.
    """
    password, public_key_b64, host, port = runtime.read_iperf_profile_material(endpoint)
    return (
        "# Written by the PRTG-NATS web platform. Do not edit by hand.\n"
        f"# Endpoint {endpoint} on {host}:{port}\n"
        f"IPERF3_PASSWORD={password}\n"
        f"IPERF3_PUBLIC_KEY_B64={public_key_b64}\n"
    )


async def _deploy_endpoint_profiles(
    context: JobContext,
    connection: ProbeConnection,
    definition: SensorDefinition,
    username: str,
) -> None:
    endpoints = context.runtime.list_iperf_endpoints()
    if not endpoints:
        await context.log(
            "jobs.sensor.no_endpoints",
            level=LogLevel.WARNING,
            params={"probe": username, "sensor": definition.name},
            target=username,
        )
        return

    for endpoint in endpoints:
        content = endpoint_profile_content(context.runtime, endpoint.name)
        await context.helper.write_profile(
            connection, definition.name, endpoint.name, content
        )
        context.runtime.remember_iperf(username, endpoint.name)
        await context.log(
            "jobs.sensor.profile_deployed",
            params={"probe": username, "endpoint": endpoint.name},
            target=username,
        )

    if len(endpoints) == 1:
        # With exactly one endpoint its profile is also "default", so nobody
        # has to name a profile while there is nothing to distinguish.
        await context.helper.write_profile(
            connection,
            definition.name,
            "default",
            endpoint_profile_content(context.runtime, endpoints[0].name),
        )
