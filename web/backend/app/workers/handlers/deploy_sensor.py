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

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError, RuntimeStateError
from app.core.ids import new_id
from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.infrastructure.helper_signing import HelperSigner
from app.infrastructure.probe_helper import (
    CURRENT_HELPER_VERSION,
    SENSOR_SLOTS,
    HelperResponse,
    ProbeConnection,
)
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorDefinition
from app.infrastructure.tool_catalog import (
    ToolArtifact,
    ToolCatalog,
    build_tool_envelope,
)
from app.persistence.models.inventory import (
    Deployment,
    DeploymentTarget,
    ProbeObservedState,
    ProbeRecord,
)
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
_TRANSACTION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    # What each probe ran before this rollout, from the observed cache - the
    # deployment row has carried a previous_version column since the initial
    # schema, and this is the moment that can fill it.
    installed = await _installed_versions(context, definition.name)

    cancelled_after = len(probe_usernames)
    for index, username in enumerate(probe_usernames):
        if context.cancelled:
            await context.log("jobs.cancelled", level=LogLevel.WARNING)
            cancelled_after = index
            break
        outcome = await deploy_one(context, definition, username, dry_run=dry_run)
        if outcome is None:
            succeeded.append(username)
        else:
            failed.append({"probe": username, **outcome})
        if deployment_id:
            await _record_target(
                context, deployment_id, username, outcome, installed.get(username)
            )

    # The probes the loop never reached would otherwise stand as "queued"
    # forever, for a job that is no longer running.
    if deployment_id and cancelled_after < len(probe_usernames):
        for username in probe_usernames[cancelled_after:]:
            await _record_target(
                context,
                deployment_id,
                username,
                None,
                installed.get(username),
                status=JobStatus.CANCELLED,
            )

    await context.jobs.finish_step(
        context.job,
        "verify",
        JobStepStatus.SUCCEEDED if not failed else JobStepStatus.FAILED,
    )

    if deployment_id:
        await _finalise_deployment(
            context,
            deployment_id,
            succeeded,
            failed,
            cancelled=cancelled_after < len(probe_usernames),
        )

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
        connection = ProbeConnection.for_probe(inventory)
        # Read before the commit step below records the sensor, because that is
        # what tells a first rollout from a repeat - and the two treat the
        # endpoint assignment differently.
        first_rollout = definition.name not in inventory.assigned_sensors

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
        if not dry_run:
            info = await _ensure_sensor_deployment_helper(
                context, connection, username, info
            )

        artifact: ToolArtifact | None = None
        tool_source: str | None = None
        if definition.managed_tool:
            tool_catalog = ToolCatalog(context.settings.tool_source_dir)
            policy_minimum = tool_catalog.system_fallback_minimum(
                definition.managed_tool
            )
            if definition.managed_tool_fallback_min_version != policy_minimum:
                raise RuntimeStateError(
                    params={"path": str(definition.directory / "manifest.env")},
                    details=(
                        f"sensor {definition.name} declares system-tool minimum "
                        f"{definition.managed_tool_fallback_min_version or 'none'}, "
                        f"but release policy requires {policy_minimum}"
                    ),
                )
            platform = info.value("platform")
            if not platform and dry_run:
                await context.log(
                    "jobs.sensor.dry_run_platform_deferred",
                    params={"probe": username, "sensor": definition.name},
                    target=username,
                )

            if not platform:
                if not dry_run:
                    raise RuntimeStateError(
                        params={"path": str(context.settings.libexec_dir)},
                        details=(
                            "the updated probe helper did not report a userspace "
                            "platform"
                        ),
                    )
            else:
                if tool_catalog.has_managed_artifact(definition.managed_tool, platform):
                    artifact = tool_catalog.select(definition.managed_tool, platform)
                    if artifact.version != definition.managed_tool_version:
                        raise RuntimeStateError(
                            params={"path": str(definition.directory / "manifest.env")},
                            details=(
                                f"sensor {definition.name} requires "
                                f"{definition.managed_tool_version}, but the release "
                                f"catalogue contains {artifact.version}"
                            ),
                        )
                    artifact.read_verified()
                    tool_source = "managed"
                else:
                    tool_catalog.validate_system_fallback(
                        definition.managed_tool, platform
                    )
                    tool_source = "system"

        if dry_run:
            await context.log(
                "jobs.sensor.dry_run",
                params={
                    "probe": username,
                    "sensor": definition.name,
                    "version": definition.version,
                    "slots": ", ".join(_slots_for(definition)),
                    "tool_source": tool_source or "deferred",
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
        if artifact is not None:
            envelope = build_tool_envelope(artifact)
            signature = HelperSigner(context.settings).sign(envelope.encode("utf-8"))
            await context.helper.sensor_tool_stage(
                connection,
                transaction,
                definition.name,
                envelope,
                signature,
            )
            await context.log(
                "jobs.sensor.staged",
                params={
                    "probe": username,
                    "slot": f"tool:{artifact.name}/{artifact.platform}",
                    "bytes": artifact.size,
                },
                target=username,
            )
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
        await _commit_sensor(context, connection, transaction)
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
            await _deploy_endpoint_profiles(
                context, connection, definition, username, seed=first_rollout
            )
        # The same for the variants an operator configured: whoever deploys the
        # sensor deploys what it needs to work with it.
        await deploy_assigned_variants(context, connection, definition, username)
        return None

    except AppError as error:
        await _try_rollback(context, username, transaction)
        recovery_transaction = _reported_active_transaction(error) or transaction
        details = _sensor_recovery_details(
            error.details, definition.name, username, recovery_transaction
        )
        await context.log(
            "jobs.sensor.failed",
            level=LogLevel.ERROR,
            params={
                "probe": username,
                "sensor": definition.name,
                "reason": error.code,
            },
            target=username,
            raw=details,
        )
        return {
            "code": error.code,
            "details": details,
        }
    except Exception as exc:
        await _try_rollback(context, username, transaction)
        details = _sensor_recovery_details(
            f"{type(exc).__name__}: {exc}", definition.name, username, transaction
        )
        await context.log(
            "jobs.sensor.failed",
            level=LogLevel.ERROR,
            params={
                "probe": username,
                "sensor": definition.name,
                "reason": "internal.unexpected",
            },
            target=username,
            raw=details,
        )
        return {"code": "internal.unexpected", "details": details}


def _sensor_recovery_details(
    details: str | None, sensor: str, username: str, transaction: str
) -> str:
    """Attach the exact fail-closed recovery action to a rollout failure."""
    recovery = (
        f"Sensor: {sensor}\n"
        f"Transaction: {transaction}\n"
        "If this transaction remains active, run:\n"
        f"sudo ./prtg-nats sensor recover {sensor} {username} "
        f"--transaction {transaction}"
    )
    return f"{details.rstrip()}\n\n{recovery}" if details else recovery


def _reported_active_transaction(error: AppError) -> str | None:
    """Return only the helper's validated, structured blocking transaction."""
    active_transaction = error.params.get("active_transaction")
    if not isinstance(active_transaction, str):
        return None
    if _TRANSACTION_PATTERN.fullmatch(active_transaction) is None:
        return None
    return active_transaction


async def _ensure_sensor_deployment_helper(
    context: JobContext,
    connection: ProbeConnection,
    username: str,
    info: HelperResponse,
) -> HelperResponse:
    """Upgrade the signed helper before any real sensor transaction starts."""
    reported = info.value("helper_version") or ""
    if reported.isdigit() and int(reported) >= CURRENT_HELPER_VERSION:
        return info

    asset = context.settings.libexec_dir / "prtg-nats-probe-helper"
    if not asset.is_file():
        raise RuntimeStateError(
            params={"path": str(asset)}, details="the probe helper asset is missing"
        )
    script = asset.read_text(encoding="utf-8")
    signature = HelperSigner(context.settings).sign(asset.read_bytes())
    response = await context.helper.helper_update(connection, script, signature)
    await context.log(
        "jobs.probe.helper_sent",
        params={
            "probe": username,
            "version": response.value("version") or str(CURRENT_HELPER_VERSION),
        },
        target=username,
        raw=response.raw,
    )
    refreshed = await context.helper.probe_info(connection)
    installed = refreshed.value("helper_version") or ""
    if not installed.isdigit() or int(installed) < CURRENT_HELPER_VERSION:
        raise RuntimeStateError(
            params={"path": str(asset)},
            details=("the probe still reports an older helper after its signed update"),
        )
    await context.log(
        "jobs.probe.helper_updated",
        params={"probe": username, "version": installed},
        target=username,
    )
    return refreshed


async def _commit_sensor(
    context: JobContext, connection: ProbeConnection, transaction: str
) -> None:
    """Confirm an idempotent commit once after any ambiguous helper failure.

    The v8 helper records a bounded commit tombstone before it answers. A
    second request therefore covers both possibilities: the first request was
    rejected before it committed, or it committed and the answer was lost or
    could not be parsed. Every AppError is ambiguous at this boundary; only a
    second matching commit can resolve it.
    """
    try:
        await context.helper.sensor_commit(connection, transaction)
    except AppError:
        await context.helper.sensor_commit(connection, transaction)


async def _try_rollback(context: JobContext, username: str, transaction: str) -> None:
    """Best effort. The probe rolls back on its own when activation fails; this
    covers the case where we failed before or after that point."""
    try:
        inventory = context.runtime.read_probe(username)
        connection = ProbeConnection.for_probe(inventory)
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


async def _installed_versions(context: JobContext, sensor_name: str) -> dict[str, str]:
    """Which version each probe reports installed, by NATS account."""
    versions: dict[str, str] = {}
    rows = await context.db.execute(
        select(ProbeRecord.nats_username, ProbeObservedState.document).join(
            ProbeObservedState, ProbeObservedState.probe_id == ProbeRecord.id
        )
    )
    for username, document in rows:
        for entry in document.get("sensors", []):
            if entry.get("name") == sensor_name and entry.get("version"):
                versions[username] = str(entry["version"])
    return versions


async def _record_target(
    context: JobContext,
    deployment_id: str,
    username: str,
    outcome: dict[str, str] | None,
    previous_version: str | None,
    *,
    status: JobStatus | None = None,
) -> None:
    target = await context.db.scalar(
        select(DeploymentTarget).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.probe_label == username,
        )
    )
    if target is None:
        return
    target.status = status or (
        JobStatus.SUCCESSFUL if outcome is None else JobStatus.FAILED
    )
    target.finished_at = datetime.now(UTC)
    target.previous_version = previous_version
    if outcome is not None:
        target.error_code = outcome.get("code")
        target.error_details = outcome.get("details")


async def _finalise_deployment(
    context: JobContext,
    deployment_id: str,
    succeeded: list[str],
    failed: list[dict[str, str]],
    *,
    cancelled: bool = False,
) -> None:
    deployment = await context.db.get(Deployment, deployment_id)
    if deployment is None:
        return
    if cancelled:
        deployment.status = JobStatus.CANCELLED
    elif failed and succeeded:
        deployment.status = JobStatus.PARTIALLY_SUCCESSFUL
    elif failed:
        deployment.status = JobStatus.FAILED
    else:
        deployment.status = JobStatus.SUCCESSFUL


DEFAULT_PROFILE = "default"


def default_endpoint(runtime: RuntimeFileStore, username: str) -> str | None:
    """Which endpoint the profile called "default" stands for on one probe.

    The alias exists so nobody has to name a profile while there is nothing to
    tell apart. That reasoning ends with the second endpoint: "default" has no
    defined meaning from then on, and a copy left behind keeps the credentials
    of whichever endpoint was once alone - a host that may since have been
    decommissioned, rotated, or handed to somebody else. So the alias is not
    written once and forgotten; it tracks the state in both directions, which
    is what sync_default_profile is for.

    What counts is what this probe holds, not what the installation knows. An
    endpoint revoked here is one this probe may no longer measure against, and
    an alias standing in for it would hand back exactly the credentials the
    revoke took away.
    """
    registered = {endpoint.name for endpoint in runtime.list_iperf_endpoints()}
    held = [name for name in runtime.assigned_iperf(username) if name in registered]
    return held[0] if len(held) == 1 else None


async def sync_default_profile(
    context: JobContext,
    connection: ProbeConnection,
    sensor: str,
    username: str,
) -> None:
    """Bring "default" on one probe in line with what that probe holds.

    Called wherever the endpoints of a probe or their credentials change,
    because every one of those moments can leave the alias behind: a second
    endpoint makes it ambiguous, a rotation makes it wrong, and losing the last
    one makes it point at nothing.
    """
    alias = default_endpoint(context.runtime, username)
    if alias is None:
        # rm -f on the probe: removing one that was never written is not an
        # error, and asking first would only cost a second round trip.
        await context.helper.remove_profile(connection, sensor, DEFAULT_PROFILE)
        await context.log(
            "jobs.sensor.default_cleared",
            params={"probe": username, "sensor": sensor},
            target=username,
        )
        return
    await context.helper.write_profile(
        connection,
        sensor,
        DEFAULT_PROFILE,
        endpoint_profile_content(context.runtime, alias),
    )
    await context.log(
        "jobs.sensor.default_deployed",
        params={"probe": username, "endpoint": alias},
        target=username,
    )


def endpoint_profile_content(runtime: RuntimeFileStore, endpoint: str) -> str:
    """The credential profile for one endpoint, as the sensor reads it.

    Shared with the rotation job, which writes the very same file when the
    password changes. Two renderings of one format would be two chances for the
    probes to end up with something the sensor cannot parse.

    Host, port and user name are in it, not only in the comment above them:
    with those the profile describes a measurement path rather than only the
    secret to walk it, and a second endpoint becomes a second sensor in PRTG
    carrying one parameter instead of four.
    """
    material = runtime.read_iperf_profile_material(endpoint)
    return (
        "# Written by the PRTG-NATS web platform. Do not edit by hand.\n"
        f"# Endpoint {endpoint} on {material.host}:{material.port}\n"
        f"IPERF3_HOST={material.host}\n"
        f"IPERF3_PORT={material.port}\n"
        f"IPERF3_USERNAME={material.username}\n"
        f"IPERF3_PASSWORD={material.password}\n"
        f"IPERF3_PUBLIC_KEY_B64={material.public_key_b64}\n"
    )


async def deploy_variant(
    context: JobContext,
    connection: ProbeConnection,
    definition: SensorDefinition,
    username: str,
    profile: str,
) -> None:
    """Put one variant on one probe: its files first, then its profile.

    The order is not a preference. A sensor reads the file paths out of the
    profile and checks that they exist - wlan-auth refuses with "file-missing"
    otherwise - so a profile that arrives before its certificate points at
    nothing for as long as the transfer takes.
    """
    for entry in context.runtime.list_sensor_profile_files(definition.name, profile):
        payload = context.runtime.read_sensor_profile_file(
            definition.name, profile, entry.key
        )
        await context.helper.write_profile_file(
            connection, definition.name, profile, entry.filename, payload
        )
        await context.log(
            "jobs.sensor.profile_file_deployed",
            params={
                "probe": username,
                "profile": profile,
                "file": entry.filename,
                "bytes": entry.size_bytes,
            },
            target=username,
        )

    content = context.runtime.sensor_profile_content(definition.name, profile)
    await context.helper.write_profile(connection, definition.name, profile, content)
    context.runtime.assign_profile(username, definition.name, profile)
    # Its own message rather than the endpoint one: both write a profile, but a
    # variant is something an operator filled in and an endpoint is a machine
    # somebody set up. A job log that calls the first an endpoint sends whoever
    # reads it looking for a host that does not exist.
    await context.log(
        "jobs.sensor.variant_deployed",
        params={"probe": username, "variant": profile},
        target=username,
    )


async def deploy_assigned_variants(
    context: JobContext,
    connection: ProbeConnection,
    definition: SensorDefinition,
    username: str,
) -> None:
    """Every variant this probe is meant to hold, after the sensor is on it.

    Deliberately after the transaction, for the same reason the endpoint
    credentials are: the self-test proves the sensor can run, not that it may
    measure, and a variant that fails to transfer must not roll the sensor
    back to the version before.
    """
    for profile in context.runtime.assigned_profiles(username, definition.name):
        if not context.runtime.sensor_profile_exists(definition.name, profile):
            # The assignment outlived the variant - someone deleted it while a
            # probe was unreachable. Saying so beats a stack trace.
            await context.log(
                "jobs.sensor.profile_missing",
                level=LogLevel.WARNING,
                params={"probe": username, "profile": profile},
                target=username,
            )
            continue
        await deploy_variant(context, connection, definition, username, profile)


async def _deploy_endpoint_profiles(
    context: JobContext,
    connection: ProbeConnection,
    definition: SensorDefinition,
    username: str,
    *,
    seed: bool,
) -> None:
    """The credentials of the endpoints this probe measures against.

    Which ones those are is the probe's own assignment, not the whole registry.
    A rollout that deployed everything would silently undo a revoke - the one
    operation whose entire point is that a probe stops holding credentials -
    and it would spread every endpoint's password across every probe that
    happens to run the sensor.

    Only the first rollout of this sensor to this probe seeds the assignment
    with every registered endpoint, and that is what makes the promise
    "whoever deploys the sensor deploys the credentials with it" hold for a new
    probe. Afterwards an empty assignment means what it says - everything was
    revoked - rather than "nothing has happened here yet". The two are
    indistinguishable from the sidecar alone, because it is deleted once its
    last entry goes.
    """
    registered = context.runtime.list_iperf_endpoints()
    if not registered:
        await context.log(
            "jobs.sensor.no_endpoints",
            level=LogLevel.WARNING,
            params={"probe": username, "sensor": definition.name},
            target=username,
        )
        # Still, deliberately: with the last endpoint gone the alias points at
        # a host that no longer answers, and this is the moment we are talking
        # to the probe anyway.
        await sync_default_profile(context, connection, definition.name, username)
        return

    if seed:
        endpoints = list(registered)
    else:
        assigned = set(context.runtime.assigned_iperf(username))
        endpoints = [endpoint for endpoint in registered if endpoint.name in assigned]

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

    await sync_default_profile(context, connection, definition.name, username)
