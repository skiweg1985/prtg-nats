"""Server-side maintenance, natively.

Certificate renewal, backup, verification and the stack restart used to go
through the shell tooling; each is now a Python service with the same file
formats and the same guarantees, and the shell scripts are gone.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domain.enums import LogLevel
from app.infrastructure.docker import StackContainer
from app.services.provisioning import ProvisioningService
from app.services.verification import StackVerification
from app.workers.context import JobContext

SETUP_STEPS: tuple[str, ...] = ("initialise", "start_containers", "verify")
SETUP_JOB_TYPE = "system.setup"

RENEW_CERTIFICATE_STEPS: tuple[str, ...] = ("renew", "restart", "verify")
RENEW_CERTIFICATE_JOB_TYPE = "certificate.renew"

BACKUP_STEPS: tuple[str, ...] = ("backup", "verify")
BACKUP_JOB_TYPE = "system.backup"

EXPORT_STEPS: tuple[str, ...] = ("export", "verify")
EXPORT_JOB_TYPE = "system.export"

VERIFY_STEPS: tuple[str, ...] = ("verify",)
VERIFY_JOB_TYPE = "system.verify"

RESTART_STEPS: tuple[str, ...] = ("restart", "wait_healthy")
RESTART_JOB_TYPE = "system.restart"


async def _log_checks(context: JobContext, checks: list[Any]) -> bool:
    all_ok = True
    for check in checks:
        await context.log(
            "jobs.system.check",
            level=LogLevel.INFO if check.ok else LogLevel.ERROR,
            params={"check": check.name, "ok": str(check.ok).lower()},
            raw=check.detail or None,
        )
        all_ok = all_ok and check.ok
    return all_ok


async def setup_runtime(context: JobContext) -> dict[str, Any]:
    """First-run initialisation: CA, server certificate, management key,
    shared account, server configuration, public material.

    What `prtg-nats setup` used to script, as a job with an audit trail. The
    containers pick the files up on their next (re)start - compose mounts the
    runtime directory, so no recreate is needed.
    """
    provisioning = ProvisioningService(context.settings, context.docker)

    await context.step("initialise")
    # Key generation is seconds of CPU; run it in a thread so it neither
    # freezes the event loop nor holds the job transaction's write lock.
    await asyncio.to_thread(provisioning.initialise_runtime)
    await context.log("jobs.system.runtime_initialised")

    await context.step("start_containers")
    if context.docker.available:
        # The proxy as well as NATS: initialisation is what creates the
        # interface certificate, and Caddy has been failing to start without
        # it. Both pick up their files on this restart.
        for container in (StackContainer.NATS, StackContainer.WEB_PROXY):
            state = await context.docker.inspect(container)
            if state.exists:
                await context.docker.restart(container)
                await context.log(
                    "jobs.system.container_restarted",
                    params={"container": container.value},
                )
        state = await context.docker.wait_healthy(StackContainer.NATS)
        await context.log(
            "jobs.system.container_state",
            level=LogLevel.INFO if state.health == "healthy" else LogLevel.WARNING,
            params={
                "container": StackContainer.NATS.value,
                "status": state.status or "unknown",
                "health": state.health or "unknown",
            },
        )
    else:
        await context.log("jobs.system.docker_unavailable_note", level=LogLevel.WARNING)

    await context.step("verify")
    checks = await StackVerification(context.settings).run(
        live=context.docker.available
    )
    ok = await _log_checks(context, checks)
    return {"initialised": True, "verified": ok}


async def renew_certificate(context: JobContext) -> dict[str, Any]:
    provisioning = ProvisioningService(context.settings, context.docker)

    await context.step("renew")
    # Key generation off the event loop, same as in setup.
    await asyncio.to_thread(provisioning.renew_server_certificate)
    # The download endpoint has to keep serving the CA that signed the new
    # certificate; republishing is cheap and makes the pair visibly consistent.
    provisioning.publish_public_material()
    await context.log("jobs.system.certificate_renewed")

    await context.step("restart")
    if context.docker.available:
        # Both leaves were renewed, so both consumers have to re-read them.
        # Leaving the proxy alone would serve the old interface certificate
        # until something else happened to restart it.
        for container in (StackContainer.NATS, StackContainer.WEB_PROXY):
            state = await context.docker.inspect(container)
            if not state.exists:
                continue
            await context.docker.restart(container)
            await context.log(
                "jobs.system.container_restarted",
                params={"container": container.value},
            )
        await context.docker.wait_healthy(StackContainer.NATS)
    else:
        await context.log("jobs.system.docker_unavailable_note", level=LogLevel.WARNING)

    await context.step("verify")
    checks = await StackVerification(context.settings).run(
        live=context.docker.available
    )
    ok = await _log_checks(context, checks)
    return {"renewed": True, "verified": ok}


async def backup(context: JobContext) -> dict[str, Any]:
    provisioning = ProvisioningService(context.settings, context.docker)

    await context.step("backup")
    result = await provisioning.backup_jetstream()
    await context.log(
        "jobs.system.backup_created",
        params={"archive": result.archive, "bytes": result.size_bytes},
    )

    await context.step("verify")
    await context.log("jobs.system.backup_checksum", params={"sha256": result.sha256})
    return {
        "archive": result.archive,
        "sha256": result.sha256,
        "size_bytes": result.size_bytes,
    }


async def export_runtime(context: JobContext) -> dict[str, Any]:
    """Archive runtime/ so the installation exists somewhere other than here.

    The JetStream backup covers message data; this covers the CA key, the
    accounts, the inventory and the database - the parts that cannot be
    rebuilt from the repository. Runs without touching NATS: reading files is
    consistent enough for state that only this process writes.
    """
    provisioning = ProvisioningService(context.settings, context.docker)

    await context.step("export")
    # Compressing the whole runtime is disk-bound work; keep it off the loop.
    result = await asyncio.to_thread(provisioning.export_runtime)
    await context.log(
        "jobs.system.export_created",
        params={"archive": result.archive, "bytes": result.size_bytes},
    )

    await context.step("verify")
    await context.log("jobs.system.backup_checksum", params={"sha256": result.sha256})
    return {
        "archive": result.archive,
        "sha256": result.sha256,
        "size_bytes": result.size_bytes,
    }


async def verify(context: JobContext) -> dict[str, Any]:
    await context.step("verify")
    checks = await StackVerification(context.settings).run(live=True)
    ok = await _log_checks(context, checks)
    if not ok:
        # The job fails so the outcome is a status, not a log to read; every
        # failed check is already in the log with its detail.
        failed = [check.name for check in checks if not check.ok]
        return {"verified": False, "failed": failed, "succeeded": []}
    return {"verified": True}


async def restart_nats(context: JobContext) -> dict[str, Any]:
    """Restart the NATS container.

    Every probe and the PRTG core reconnect afterwards, which is why this is
    behind system.restart and behind a confirmation in the interface.
    """
    await context.step("restart")
    await context.docker.restart(StackContainer.NATS)
    await context.log(
        "jobs.system.container_restarted",
        params={"container": StackContainer.NATS.value},
    )

    await context.step("wait_healthy")
    state = await context.docker.wait_healthy(StackContainer.NATS)
    healthy = state.health == "healthy" or (state.running and state.health is None)
    await context.log(
        "jobs.system.container_state",
        level=LogLevel.INFO if healthy else LogLevel.WARNING,
        params={
            "container": StackContainer.NATS.value,
            "status": state.status or "unknown",
            "health": state.health or "unknown",
        },
    )
    return {"running": state.running, "health": state.health}
