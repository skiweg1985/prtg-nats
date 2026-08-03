"""Configure, reconcile, and retire probes - the lifecycle jobs.

These replace the retired manage-probes configure/apply/unenroll paths. The
probe-side transaction protocol does the heavy lifting; the handlers decide
what goes into it and report each step.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ProbeRejectedError, RuntimeStateError
from app.domain import probe_config
from app.domain.enums import CertificateKind, LogLevel
from app.domain.models import (
    DesiredProbeState,
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

ROTATE_STEPS: tuple[str, ...] = ("rotate_server", "reconfigure_probe", "verify")
ROTATE_JOB_TYPE = "credential.rotate"


def _connection(context: JobContext, username: str) -> ProbeConnection:
    inventory = context.runtime.read_probe(username)
    return ProbeConnection(
        nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
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

        await context.step("activate")
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


async def unenroll(context: JobContext) -> dict[str, Any]:
    """Remove the management access and the inventory.

    The probe keeps running and stays connected to NATS; what disappears is
    our ability to manage it. Deleting the NATS account is a separate,
    deliberate action - a probe may be unenrolled while it keeps reporting.
    """
    username: str = context.payload["probe"]

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
    return {"probe": username}


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
