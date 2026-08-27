"""Finishing an enrolment the host started.

The host has already installed the restricted management access and reported
its SSH host keys. What is left is everything the platform owns: pinning those
keys, creating the NATS account, writing the inventory, and configuring the
probe over the channel that was just installed.

The order matters and is the same order the shell tooling used. Nothing is
written to the inventory until the management channel has actually answered -
an inventory entry for a probe that cannot be reached is worse than no entry,
because everything downstream believes it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.errors import HostAlreadyEnrolledError, RuntimeStateError
from app.domain import probe_config
from app.domain.enums import LogLevel
from app.domain.models import parse_probe_info
from app.infrastructure import known_hosts
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.probe_helper import ProbeConnection
from app.infrastructure.probe_helper.protocol import normalise_optional
from app.services.provisioning import ProvisioningService
from app.workers.context import JobContext
from app.workers.handlers import probe_lifecycle

ENROLL_STEPS: tuple[str, ...] = (
    "pin_host_key",
    "ensure_account",
    "verify_access",
    "write_inventory",
    "install_ca",
    "configure",
)
ENROLL_JOB_TYPE = "probe.enroll"


async def enroll(context: JobContext) -> dict[str, Any]:
    """Take over from the bootstrap script and finish the job."""
    created: dict[str, Any] = {
        "pinned_host_key": False,
        "created_account": False,
        "wrote_inventory": False,
    }
    try:
        return await _enroll(context, created)
    except Exception:
        # What this run left behind, said out loud. The runner keeps a result
        # only for a job that succeeded, so without this line a failure half
        # way through is an operator guessing which of these three happened -
        # and each has its own way back (delete the credential, forget the
        # host key, remove the probe).
        await context.log(
            "jobs.probe.enroll_incomplete",
            level=LogLevel.WARNING,
            params={key: str(value).lower() for key, value in created.items()},
        )
        raise


async def _enroll(context: JobContext, created: dict[str, Any]) -> dict[str, Any]:
    payload = context.payload
    username: str = payload["nats_username"]
    host: str = payload["host"]
    port: int = int(payload.get("ssh_port") or 22)
    reported_keys: list[str] = list(payload.get("host_keys") or [])

    # Checked again here, not only when the invitation was created. The
    # address may have come from the callback rather than the operator, and
    # the inventory may have gained an entry in between.
    #
    # Two entries for one host share a management access - it belongs to the
    # host, not to the entry - so retiring either one revokes it for both and
    # leaves the survivor unreachable while still connected to NATS.
    claimed = context.runtime.probe_username_for_host(host, excluding=username)
    if claimed:
        raise HostAlreadyEnrolledError(
            params={"host": host, "probe": claimed},
            details=f"{host} is already enrolled as {claimed}",
        )

    # --- 1. Pin what the host reported -------------------------------------
    # Before anything else: every step after this one talks to that host, and
    # this is what decides it is the right one.
    await context.step("pin_host_key")
    keys = tuple(
        key
        for key in (known_hosts.HostKey.parse(line) for line in reported_keys)
        if key
    )
    if not keys:
        raise RuntimeStateError(
            params={"host": host}, details="the host reported no usable SSH host keys"
        )
    written = known_hosts.pin(context.settings.ssh_known_hosts_path, host, port, keys)
    created["pinned_host_key"] = bool(written)
    await context.log(
        "jobs.probe.host_key_pinned",
        params={
            "host": host,
            "fingerprints": ", ".join(key.fingerprint for key in (written or keys)),
            "new": str(bool(written)).lower(),
        },
    )

    # --- 2. The NATS account -----------------------------------------------
    await context.step("ensure_account")
    nats = NatsRuntime(context.settings)
    provisioning = ProvisioningService(context.settings, context.docker)
    if nats.account_exists(username):
        await context.log("jobs.probe.account_exists", params={"username": username})
    else:
        await provisioning.create_account(username)
        created["created_account"] = True
        await context.log("jobs.probe.account_created", params={"username": username})

    # --- 3. Does the management channel answer? -----------------------------
    # The inventory is written only after this. The shell tooling refused to
    # leave an entry behind for a probe it could not reach, and so does this.
    await context.step("verify_access")
    connection = ProbeConnection(nats_username=username, host=host, port=port)
    response = await context.helper.probe_info(connection)
    observed = parse_probe_info(username, response, _now())
    await context.log(
        "jobs.probe.access_verified",
        params={
            "host": host,
            "package": observed.package_version or "none",
            "service": (observed.service.value if observed.service else "unknown"),
        },
    )
    # Stopped here rather than four steps later in the configuration, which is
    # where it used to surface as a refused request. Nothing is in the
    # inventory yet, so an operator who has to start over is not cleaning up
    # after this run first. The bootstrap's own words carry the reason the
    # package never made it, which is otherwise lost by the time it matters.
    probe_lifecycle.require_package(
        username, observed, details=payload.get("package_error") or None
    )

    # --- 4. The inventory ---------------------------------------------------
    await context.step("write_inventory")
    probe_name: str = payload.get("probe_name") or probe_config.default_probe_name(host)
    # Keep whatever the probe already carries: re-enrolling a host that PRTG
    # already knows must not hand it a new identity and orphan its history.
    #
    # The key comes off the probe when the runtime has none, which is the case
    # on every first enrolment and after a runtime was rebuilt. Read from the
    # answer rather than from ObservedProbeState, which keeps only whether a
    # key exists: the value is a secret, and its place is the inventory file,
    # not the observation cache the interface reads.
    probe_id = observed.probe_id or probe_config.generate_probe_id()
    access_key = (
        context.runtime.read_access_key(username)
        or normalise_optional(response.value("access_key"))
        or probe_config.default_access_key(probe_name)
    )
    context.runtime.write_probe_inventory(
        nats_username=username,
        ssh_host=host,
        ssh_port=port,
        probe_id=probe_id,
        access_key=access_key,
        probe_name=probe_name,
    )
    created["wrote_inventory"] = True
    await context.log(
        "jobs.probe.inventory_written",
        params={"username": username, "probe_name": probe_name},
    )

    # --- 5. The CA ----------------------------------------------------------
    # The bootstrap script already installed it; this proves it over the
    # management channel rather than trusting the report.
    await context.step("install_ca")
    ca_path = context.settings.cert_dir / "ca.pem"
    if not ca_path.is_file():
        raise RuntimeStateError(details="CA is missing; initialise the runtime first")
    await context.helper.install_ca(connection, ca_path.read_text(encoding="utf-8"))
    await context.log("jobs.probe.ca_installed", params={"probe": username})

    # --- 6. The configuration ----------------------------------------------
    await context.step("configure")
    await probe_lifecycle.run_config_transaction(
        context, username, probe_name=probe_name
    )
    await context.log(
        "jobs.probe.enrolled", params={"username": username, "host": host}
    )

    return {
        **created,
        "nats_username": username,
        "host": host,
        "probe_name": probe_name,
        "access_key_available": True,
    }


def _now() -> datetime:
    return datetime.now(UTC)
