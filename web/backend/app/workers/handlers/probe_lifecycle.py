"""Configure, reconcile, and retire probes - the lifecycle jobs.

These replace the retired manage-probes configure/apply/unenroll paths. The
probe-side transaction protocol does the heavy lifting; the handlers decide
what goes into it and report each step.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from app.core.errors import (
    ProbePackageMissingError,
    ProbeRejectedError,
    RuntimeStateError,
)
from app.domain import probe_config
from app.domain.enums import CertificateKind, LogLevel
from app.domain.models import (
    DesiredProbeState,
    ObservedProbeState,
    parse_probe_info,
    parse_sensor_list,
)
from app.domain.reconciliation import build_plan, find_deviations
from app.infrastructure.certificates import read_certificate
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.probe_helper import ProbeConnection
from app.services.provisioning import ProvisioningService
from app.workers.context import JobContext
from app.workers.handlers import deploy_sensor as deploy_sensor_handler
from app.workers.handlers import sensor_actions

CONFIGURE_STEPS: tuple[str, ...] = (
    "resolve_identity",
    "render_config",
    "stage_files",
    "activate",
    "commit",
)
CONFIGURE_JOB_TYPE = "probe.configure"

RECONCILE_STEPS: tuple[str, ...] = ("plan", "apply", "verify")
RECONCILE_JOB_TYPE = "probe.reconcile"

UNENROLL_STEPS: tuple[str, ...] = ("revoke_access", "remove_inventory")
UNENROLL_JOB_TYPE = "probe.unenroll"

# The optional parts of a retirement. Each one is a step only when it was
# asked for, so the progress display never shows work that will not happen.
REMOVE_SENSORS_STEP = "remove_sensors"
UNINSTALL_MPP_STEP = "uninstall_mpp"
DELETE_ACCOUNT_STEP = "delete_account"

ROTATE_STEPS: tuple[str, ...] = ("rotate_server", "reconfigure_probe", "verify")
ROTATE_JOB_TYPE = "credential.rotate"


def _connection(context: JobContext, username: str) -> ProbeConnection:
    inventory = context.runtime.read_probe(username)
    return ProbeConnection(
        nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
    )


def require_package(
    username: str, observed: ObservedProbeState, *, details: str | None = None
) -> None:
    """Refuse to configure a probe that carries no MPP package.

    Nothing downstream can succeed without it, and the failure it produces
    names a systemd unit rather than the missing package. The way back is not
    a retry: the package arrives with the bootstrap script and with nothing
    else, so a retirement that uninstalled it has to be followed by a fresh
    invitation, not by another run of the same job.
    """
    if observed.package_version:
        return
    raise ProbePackageMissingError(
        params={"probe": username},
        details=details
        or (
            "no prtgmpprobe package on the probe; run the bootstrap command "
            "from a fresh invitation, or install-mpp.sh on the probe itself"
        ),
    )


async def run_config_transaction(
    context: JobContext, username: str, *, probe_name: str | None
) -> dict[str, str]:
    """Render the configuration and roll it out transactionally.

    stage credentials -> write-config -> activate (restarts the service and
    verifies) -> commit. On any failure after staging, roll back - the probe
    restores its previous configuration.
    """
    inventory = context.runtime.read_probe(username)
    connection = _connection(context, username)
    site = context.runtime.site_settings()
    nats = NatsRuntime(context.settings)

    await context.step("resolve_identity")
    # Asked before anything is staged, and asked here rather than trusted from
    # a caller: this is the one gate every configuration passes through, so a
    # probe without the package is turned away once instead of in four
    # handlers.
    require_package(
        username,
        parse_probe_info(
            username, await context.helper.probe_info(connection), datetime.now(UTC)
        ),
    )
    probe_id = inventory.probe_id or probe_config.generate_probe_id()
    resolved_name = (
        probe_name
        or inventory.probe_name
        or probe_config.default_probe_name(inventory.ssh_host)
    )
    access_key = context.runtime.read_access_key(username) or (
        probe_config.default_access_key(resolved_name)
    )
    await context.log(
        "jobs.probe.identity_resolved",
        params={"probe": username, "probe_name": resolved_name},
        target=username,
    )

    await context.step("render_config")
    if not site.nats_fqdn:
        raise RuntimeStateError(details="NATS_FQDN is not configured")
    values = probe_config.ProbeConfigValues(
        probe_id=probe_id,
        access_key=access_key,
        probe_name=resolved_name,
        nats_host=site.nats_fqdn,
        nats_port=site.nats_port,
        nats_user=username,
        nats_password=nats.read_password(username),
    )
    rendered = probe_config.render_probe_config(
        context.settings.template_dir / "mpprobe-config.yaml.template",
        values,
    )
    await context.log(
        "jobs.probe.config_rendered", params={"probe": username}, target=username
    )

    transaction = probe_config.new_transaction_id()
    # The inventory records the pending transaction first, so a crash between
    # here and commit leaves a visible trace instead of a mystery.
    context.runtime.write_probe_inventory(
        nats_username=username,
        ssh_host=inventory.ssh_host,
        ssh_port=inventory.ssh_port,
        probe_id=probe_id,
        access_key=access_key,
        probe_name=resolved_name,
        pending_transaction=transaction,
    )

    try:
        await context.step("stage_files")
        # write_config opens the transaction; the rendered configuration
        # already carries the credentials.
        await context.helper.write_config(connection, transaction, rendered)
        await context.log(
            "jobs.probe.config_staged", params={"probe": username}, target=username
        )

        # Said before the step rather than after it, because this is the one
        # that can sit there. Where the NATS account changed, the running
        # process holds on to a connection the server no longer accepts and
        # ignores SIGTERM while it retries; systemd then spends its full stop
        # timeout - about ninety seconds - before the new process starts. A
        # job that goes quiet for that long reads as a job that died.
        await context.step("activate")
        await context.log(
            "jobs.probe.config_activating", params={"probe": username}, target=username
        )
        response = await context.helper.activate(connection, transaction)
        await context.log(
            "jobs.probe.config_activated",
            params={"probe": username},
            target=username,
            raw=response.raw.strip() or None,
        )

        await context.step("commit")
        await context.helper.commit(connection, transaction)
    except Exception:
        try:
            await context.helper.rollback(connection, transaction)
            await context.log(
                "jobs.probe.config_rolled_back",
                level=LogLevel.WARNING,
                params={"probe": username},
                target=username,
            )
        except Exception:  # noqa: S110 - the original error is the story
            pass
        raise
    finally:
        context.runtime.write_probe_inventory(
            nats_username=username,
            ssh_host=inventory.ssh_host,
            ssh_port=inventory.ssh_port,
            probe_id=probe_id,
            access_key=access_key,
            probe_name=resolved_name,
            pending_transaction="",
        )

    await context.log(
        "jobs.probe.configured", params={"probe": username}, target=username
    )
    return {"probe": username, "probe_name": resolved_name, "probe_id": probe_id}


async def configure(context: JobContext) -> dict[str, Any]:
    username: str = context.payload["probe"]
    probe_name: str | None = context.payload.get("probe_name")
    return await run_config_transaction(context, username, probe_name=probe_name)


async def reconcile(context: JobContext) -> dict[str, Any]:
    """Make the probe match its desired state - the plan, executed.

    The plan is rebuilt from fresh observation inside the job rather than
    trusted from the preview: the probe may have changed since the operator
    looked, and acting on a stale plan is how automation earns distrust.
    """
    username: str = context.payload["probe"]
    desired = DesiredProbeState.from_document(context.payload.get("desired") or {})

    await context.step("plan")
    connection = _connection(context, username)
    info = await context.helper.probe_info(connection)
    observed = parse_probe_info(username, info, datetime.now(UTC))
    try:
        sensors = await context.helper.sensor_list(connection)
        observed = dataclasses.replace(observed, sensors=parse_sensor_list(sensors))
    except Exception:
        await context.log(
            "jobs.probe.finding",
            level=LogLevel.WARNING,
            params={"probe": username, "finding": "sensor list unavailable"},
        )

    definitions = context.catalog.list()
    ca_info = read_certificate(context.settings.cert_dir / "ca.pem", CertificateKind.CA)
    deviations = find_deviations(
        desired,
        observed,
        catalogue_versions={d.name: d.version for d in definitions},
        catalogue_checksums={
            d.name: f.sha256 for d in definitions if (f := d.file_for("script"))
        },
        expected_ca_sha256=ca_info.sha256,
    )
    plan = build_plan(username, deviations)
    await context.log(
        "jobs.reconcile.planned",
        params={"probe": username, "actions": len(plan.actions)},
    )
    if plan.is_empty:
        await context.step("verify")
        await context.log("jobs.reconcile.nothing_to_do", params={"probe": username})
        return {"probe": username, "actions": []}

    await context.step("apply")
    applied: list[str] = []
    for action in plan.actions:
        if context.cancelled:
            break
        await context.log(
            "jobs.reconcile.action",
            params={"kind": action.kind, **action.params},
            target=username,
        )
        if action.kind == "install_ca":
            await context.helper.install_ca(connection, context.runtime.ca_pem())
        elif action.kind in {"configure", "restart_service"}:
            # The configuration transaction activates and restarts the service,
            # which covers both kinds in one honest operation.
            await run_config_transaction(
                context, username, probe_name=desired.probe_name
            )
        elif action.kind == "deploy_sensor":
            outcome = await deploy_sensor_handler.deploy_one(
                context,
                context.catalog.get(action.params["sensor"]),
                username,
                dry_run=False,
            )
            if outcome is not None:
                raise ProbeRejectedError(
                    params={"probe": username, "command": "sensor-deploy"},
                    details=outcome.get("details"),
                )
        applied.append(action.kind)

    await context.step("verify")
    await context.log(
        "jobs.reconcile.applied", params={"probe": username, "count": len(applied)}
    )
    return {"probe": username, "actions": applied}


def unenroll_steps(
    *, remove_sensors: bool, uninstall_mpp: bool, delete_account: bool
) -> tuple[str, ...]:
    """The steps one retirement runs, in the only order they work in.

    Anything that needs the management channel has to happen before
    revoke_access, which is the step that closes it. Deleting the NATS
    account has to happen after remove_inventory, because the server refuses
    while an inventory still points at it.
    """
    steps: list[str] = []
    if remove_sensors:
        steps.append(REMOVE_SENSORS_STEP)
    if uninstall_mpp:
        steps.append(UNINSTALL_MPP_STEP)
    steps.extend(UNENROLL_STEPS)
    if delete_account:
        steps.append(DELETE_ACCOUNT_STEP)
    return tuple(steps)


async def _remove_every_sensor(
    context: JobContext, connection: ProbeConnection, username: str
) -> list[str]:
    """Take every sensor off the probe.

    Both sources are asked, because they can disagree after an interrupted
    rollout: the inventory knows what we deployed, the probe knows what it
    actually carries. Trusting the bookkeeping alone would leave behind
    precisely the sensors something already went wrong with.
    """
    names = set(context.runtime.read_probe(username).assigned_sensors)
    listed = await context.helper.sensor_list(connection)
    names.update(
        record["name"] for record in listed.records if record.get("name", "").strip()
    )

    removed: list[str] = []
    for sensor in sorted(names):
        await sensor_actions.remove_from_probe(context, connection, username, sensor)
        removed.append(sensor)
    await context.log(
        "jobs.probe.sensors_removed",
        params={"probe": username, "count": len(removed)},
    )
    return removed


async def _uninstall_probe_software(
    context: JobContext, connection: ProbeConnection, username: str
) -> str:
    """Remove the probe software, and report what was left standing."""
    response = await context.helper.mpp_uninstall(connection)
    package = response.header_fields.get("package", "none")
    await context.log(
        "jobs.probe.mpp_uninstalled",
        params={"probe": username, "package": package},
    )
    # Sensors outlive the package they were installed for. Saying so beats
    # letting the next operator find dead units on a host nobody manages.
    leftover = response.header_fields.get("sensors", "0")
    if leftover.isdigit() and int(leftover) > 0:
        await context.log(
            "jobs.probe.sensors_left_behind",
            level=LogLevel.WARNING,
            params={"probe": username, "count": leftover},
        )
    return package


async def unenroll(context: JobContext) -> dict[str, Any]:
    """Retire a probe - as far as the operator asked for.

    On its own this removes the management access and the inventory: the
    probe keeps running and stays connected to NATS, and only our ability to
    manage it disappears. The three optional parts go further, and a failure
    in either of the first two aborts the job before the access is revoked -
    a probe that could not be cleaned up has to stay reachable, or nobody
    will ever reach it again.
    """
    username: str = context.payload["probe"]
    remove_sensors = bool(context.payload.get("remove_sensors"))
    uninstall_mpp = bool(context.payload.get("uninstall_mpp"))
    delete_account = bool(context.payload.get("delete_account"))
    removed_sensors: list[str] = []
    package = "none"

    if remove_sensors:
        await context.step(REMOVE_SENSORS_STEP)
        removed_sensors = await _remove_every_sensor(
            context, _connection(context, username), username
        )

    if uninstall_mpp:
        await context.step(UNINSTALL_MPP_STEP)
        package = await _uninstall_probe_software(
            context, _connection(context, username), username
        )

    await context.step("revoke_access")
    try:
        connection = _connection(context, username)
        await context.helper.unenroll(connection)
        await context.log("jobs.probe.access_revoked", params={"probe": username})
    except Exception as exc:
        # An unreachable probe cannot revoke its own key. The inventory is
        # removed regardless - that is what the operator asked for - and the
        # leftover key on the probe is named rather than forgotten.
        await context.log(
            "jobs.probe.access_revoke_failed",
            level=LogLevel.WARNING,
            params={"probe": username},
            raw=str(exc)[:500],
        )

    await context.step("remove_inventory")
    context.runtime.remove_probe(username)
    await context.log("jobs.probe.unenrolled", params={"probe": username})

    if delete_account:
        await context.step(DELETE_ACCOUNT_STEP)
        provisioning = ProvisioningService(context.settings, context.docker)
        await provisioning.delete_account(username)
        await context.log("jobs.credential.deleted", params={"probe": username})

    return {
        "probe": username,
        "sensors_removed": removed_sensors,
        "package": package,
        "account_deleted": delete_account,
    }


async def rotate_credential(context: JobContext) -> dict[str, Any]:
    """Rotate one NATS account end to end.

    Server side first - new password, new hash, config reload - then the probe
    is reconfigured over the management channel so both sides change in one
    job. For the shared core account there is no probe; the operator updates
    the PRTG core and the job says so.
    """
    username: str = context.payload["probe"]
    provisioning = ProvisioningService(context.settings, context.docker)

    await context.step("rotate_server")
    await provisioning.rotate_account(username)
    await context.log("jobs.credential.rotated_server", params={"probe": username})

    has_probe = (context.settings.probe_dir / f"{username}.env").is_file()
    if has_probe:
        await context.step("reconfigure_probe")
        await run_config_transaction(context, username, probe_name=None)
    else:
        await context.log(
            "jobs.credential.manual_followup",
            level=LogLevel.WARNING,
            params={"probe": username},
        )

    await context.step("verify")
    return {"probe": username, "probe_reconfigured": has_probe}
