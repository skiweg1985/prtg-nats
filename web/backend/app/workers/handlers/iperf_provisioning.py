"""Setting up an iperf endpoint by reaching out to it.

The other way in, and the one that fits the usual topology. An endpoint never
needs to know this platform - it only has to answer the probes - but it does
have to be reachable from here, because that is what the management channel is.
A measurement endpoint on a public address frequently cannot reach an
installation that sits on an internal network, and the bootstrap ceremony would
demand exactly that.

So the platform signs in once as an administrator, installs the restricted
access itself, and drops the credentials it used. Everything after that is the
same as the bootstrap path, down to the same function.

Removal and rotation live here too. Both are the same channel used twice more,
and keeping them next to the run that created it means the three states of an
endpoint - set up, renewed, taken back - are read in one file.
"""

from __future__ import annotations

import secrets
from typing import Any

from app.core.errors import RuntimeStateError
from app.domain.enums import LogLevel
from app.infrastructure import known_hosts
from app.infrastructure.iperf_helper import EndpointConnection
from app.infrastructure.probe_helper import ProbeConnection
from app.infrastructure.runtime_files import ProbeInventory
from app.infrastructure.ssh_provisioning import AdminCredentials, install_access
from app.services.enrollment import IPERF_ASSETS, EnrollmentService
from app.workers.context import JobContext
from app.workers.handlers import deploy_sensor
from app.workers.handlers.deploy_sensor import (
    endpoint_profile_content,
    sync_default_profile,
)
from app.workers.handlers.iperf_enrollment import PASSWORD_BYTES, finish_setup

PROVISION_STEPS: tuple[str, ...] = (
    "pin_host_key",
    "install_access",
    "verify_access",
    "setup_endpoint",
    "write_record",
)
PROVISION_JOB_TYPE = "iperf.provision"

REMOVE_STEPS: tuple[str, ...] = (
    "revoke_profiles",
    "remove_endpoint",
    "remove_access",
    "forget_record",
)
REMOVE_JOB_TYPE = "iperf.remove"

ROTATE_STEPS: tuple[str, ...] = ("set_password", "update_record", "refresh_probes")
ROTATE_JOB_TYPE = "iperf.rotate"

# The name the management public key is written under while it is staged. Only
# the enrolment script reads it, and only from the directory it arrived in.
_KEY_FILE = "management.pub"


async def provision(context: JobContext) -> dict[str, Any]:
    payload = context.payload
    name: str = payload["name"]
    host: str = payload["host"]
    ssh_port: int = int(payload.get("ssh_port") or 22)
    iperf_port: int = int(payload.get("iperf_port") or 5201)
    username: str = payload.get("username") or "prtg-probe"
    source_cidr: str = payload["ssh_source_cidr"]
    reported_keys: list[str] = list(payload.get("host_keys") or [])

    # --- 1. Pin what the operator accepted ----------------------------------
    # The keys were read from the host by the scan route and shown to somebody
    # before any credential was typed. Pinning them here, before the sign-in
    # below, is what makes that acceptance count: the password goes to the host
    # whose key was on screen, not to whatever answers the address now.
    await context.step("pin_host_key")
    keys = tuple(
        key
        for key in (known_hosts.HostKey.parse(line) for line in reported_keys)
        if key
    )
    if not keys:
        raise RuntimeStateError(
            params={"host": host}, details="no usable SSH host keys were accepted"
        )
    written = known_hosts.pin(
        context.settings.ssh_known_hosts_path, host, ssh_port, keys
    )
    await context.log(
        "jobs.iperf.host_key_pinned",
        params={
            "host": host,
            "fingerprints": ", ".join(key.fingerprint for key in (written or keys)),
            "new": str(bool(written)).lower(),
        },
    )

    # --- 2. Install the restricted access ------------------------------------
    await context.step("install_access")
    credentials = _credentials_from(context)
    enrollment = EnrollmentService(context.db, context.settings)
    files = {name_: _asset_text(context, name_) for name_ in IPERF_ASSETS}
    files[_KEY_FILE] = enrollment.management_public_key() + "\n"

    report = await install_access(
        host=host,
        port=ssh_port,
        credentials=credentials,
        known_hosts_path=context.settings.ssh_known_hosts_path,
        files=files,
        command=[
            "bash",
            "@@STAGE@@/iperf-enroll.sh",
            "--public-key",
            f"@@STAGE@@/{_KEY_FILE}",
            "--helper",
            "@@STAGE@@/prtg-nats-iperf-helper",
            "--setup-script",
            "@@STAGE@@/setup-iperf3-endpoint.sh",
            "--source-cidr",
            source_cidr,
        ],
    )
    await context.log(
        "jobs.iperf.access_installed",
        params={
            "endpoint": name,
            "host": host,
            "admin": credentials.username,
            "allowed": source_cidr,
        },
        raw="\n".join(report.lines) or None,
    )

    # --- 3-5. The same as the bootstrap path ---------------------------------
    return await finish_setup(
        context,
        name=name,
        host=host,
        ssh_port=ssh_port,
        iperf_port=iperf_port,
        username=username,
        expected_from=source_cidr,
    )


async def remove(context: JobContext) -> dict[str, Any]:
    """Take an endpoint back off - as far as this platform can reach.

    The order is the only one that does not strand anything: the probes lose
    the credentials first, then the endpoint stops accepting them, then the
    access that did the work removes itself, and the record goes last. Reversed,
    a failure halfway would leave probes measuring against a host that no longer
    answers, with no way left to tell them.

    Each step tolerates a host that has already gone. An endpoint somebody
    decommissioned last week still has a record here, and refusing to clean that
    up because the machine is unreachable would be a record nobody can ever
    remove.
    """
    payload = context.payload
    name: str = payload["name"]
    host: str = payload["host"]
    ssh_port: int = int(payload.get("ssh_port") or 22)
    managed: bool = bool(payload.get("managed", True))
    probes: list[str] = list(payload.get("probes") or [])
    sensors: list[str] = list(payload.get("sensors") or [])

    outcome: dict[str, Any] = {
        "endpoint": name,
        "revoked_from": [],
        "endpoint_removed": False,
        "access_removed": False,
    }

    # --- 1. The probes -------------------------------------------------------
    await context.step("revoke_profiles")
    for probe in probes:
        try:
            inventory = context.runtime.read_probe(probe)
        except Exception:
            await context.log(
                "jobs.iperf.probe_skipped",
                level=LogLevel.WARNING,
                params={"endpoint": name, "probe": probe},
            )
            continue
        connection = _probe_connection(probe, inventory)
        removed = False
        for sensor in sensors:
            try:
                await context.helper.remove_profile(connection, sensor, name)
                # "default" goes with it, always. Either it was this endpoint's
                # alias and would now name a host that no longer answers, or a
                # second endpoint had already left it without a meaning. It
                # comes back on the next rollout if one endpoint is left alone.
                await context.helper.remove_profile(
                    connection, sensor, deploy_sensor.DEFAULT_PROFILE
                )
                removed = True
            except Exception:
                # One sensor on one probe that did not let go is not a reason
                # to keep the endpoint. It is a reason to say so.
                await context.log(
                    "jobs.iperf.profile_kept",
                    level=LogLevel.WARNING,
                    params={"endpoint": name, "probe": probe, "sensor": sensor},
                )
        context.runtime.forget_iperf(probe, name)
        if removed:
            outcome["revoked_from"].append(probe)
            await context.log(
                "jobs.iperf.profile_revoked",
                params={"endpoint": name, "probe": probe},
            )

    # --- 2. The endpoint -----------------------------------------------------
    # Only for one this platform set up. A host somebody else operates was
    # never ours to change, and the record here is the whole of what we have.
    await context.step("remove_endpoint")
    connection_to_endpoint = None
    if managed:
        connection_to_endpoint = EndpointConnection(name=name, host=host, port=ssh_port)
        try:
            await context.endpoints.endpoint_remove(connection_to_endpoint)
            outcome["endpoint_removed"] = True
            await context.log(
                "jobs.iperf.endpoint_removed", params={"endpoint": name, "host": host}
            )
        except Exception as exc:
            await context.log(
                "jobs.iperf.endpoint_unreachable",
                level=LogLevel.WARNING,
                params={"endpoint": name, "host": host},
                raw=str(exc),
            )
    else:
        await context.log(
            "jobs.iperf.unmanaged_left_alone", params={"endpoint": name, "host": host}
        )

    # --- 3. The access itself ------------------------------------------------
    # The last thing the channel does is take itself away.
    await context.step("remove_access")
    if connection_to_endpoint is not None:
        try:
            await context.endpoints.unenroll(connection_to_endpoint)
            outcome["access_removed"] = True
            await context.log(
                "jobs.iperf.access_removed", params={"endpoint": name, "host": host}
            )
        except Exception as exc:
            await context.log(
                "jobs.iperf.access_kept",
                level=LogLevel.WARNING,
                params={"endpoint": name, "host": host},
                raw=str(exc),
            )

    # --- 4. The record -------------------------------------------------------
    await context.step("forget_record")
    context.runtime.remove_iperf_record(name)
    known_hosts.forget(context.settings.ssh_known_hosts_path, host, ssh_port)
    await context.log("jobs.iperf.forgotten", params={"endpoint": name, "host": host})
    return outcome


async def rotate(context: JobContext) -> dict[str, Any]:
    """Give the endpoint a new password and carry it to the probes.

    One request on the endpoint - endpoint-setup is idempotent and always
    leaves it holding exactly what was just sent - and then the probes that
    measure against it, which would otherwise be locked out by the very change
    that was meant to be routine.
    """
    payload = context.payload
    name: str = payload["name"]
    host: str = payload["host"]
    ssh_port: int = int(payload.get("ssh_port") or 22)
    iperf_port: int = int(payload.get("iperf_port") or 5201)
    username: str = payload.get("username") or "prtg-probe"

    await context.step("set_password")
    connection = EndpointConnection(name=name, host=host, port=ssh_port)
    password = secrets.token_hex(PASSWORD_BYTES)
    await context.endpoints.endpoint_setup(
        connection, username=username, port=iperf_port, password=password
    )
    await context.log(
        "jobs.iperf.password_set", params={"endpoint": name, "host": host}
    )

    # The stored public key is deliberately not replaced: the key pair on the
    # endpoint is untouched by a credential change, and writing nothing over it
    # would cost every probe the ability to encrypt what it sends.
    await context.step("update_record")
    context.runtime.write_iperf_record(
        name=name,
        host=host,
        port=iperf_port,
        username=username,
        password=password,
        public_key_pem=None,
        managed=True,
        ssh_port=ssh_port,
    )

    # Every probe that holds this endpoint is locked out from the moment above
    # until it has the new password. That makes this not a convenience but the
    # repair of the state the rotation just created.
    await context.step("refresh_probes")
    refreshed = await _redeploy_profiles(context, name, payload)
    await context.log(
        "jobs.iperf.rotated",
        params={"endpoint": name, "probes": str(len(refreshed))},
    )
    return {"endpoint": name, "refreshed": refreshed}


# --- Bits --------------------------------------------------------------------


def _credentials_from(context: JobContext) -> AdminCredentials:
    """The one-time sign-in, from the values handed over out of band.

    They are in context.secrets and nowhere else - not in the payload, which is
    a database row, and not in the log.
    """
    given = context.secrets
    username = given.get("admin_username")
    if not username:
        raise RuntimeStateError(
            details="no administrator credentials were handed to this job; "
            "it cannot sign in to install the access"
        )
    return AdminCredentials(
        username=username,
        password=given.get("admin_password") or None,
        private_key=given.get("admin_private_key") or None,
        key_passphrase=given.get("admin_key_passphrase") or None,
        sudo_password=given.get("sudo_password") or None,
    )


def _asset_text(context: JobContext, name: str) -> str:
    relative = IPERF_ASSETS[name]
    path = context.settings.asset_dir / relative
    if not path.is_file():
        raise RuntimeStateError(
            params={"path": str(path)}, details=f"asset is missing: {name}"
        )
    return path.read_text(encoding="utf-8")


def _probe_connection(probe: str, inventory: ProbeInventory) -> ProbeConnection:
    return ProbeConnection(
        nats_username=probe, host=inventory.ssh_host, port=inventory.ssh_port
    )


async def _redeploy_profiles(
    context: JobContext, name: str, payload: dict[str, Any]
) -> list[str]:
    """Write the new credentials to every probe that holds this endpoint.

    "default" is refreshed along with the profile under the endpoint's own
    name. On an installation with one endpoint that alias is the profile the
    sensors actually use - it is what makes --profile unnecessary - so leaving
    it on the old password would lock out exactly the probes a rotation is
    meant to keep running, and do it silently.
    """
    probes: list[str] = list(payload.get("probes") or [])
    sensors: list[str] = list(payload.get("sensors") or [])
    content = endpoint_profile_content(context.runtime, name)
    refreshed: list[str] = []

    for probe in probes:
        try:
            inventory = context.runtime.read_probe(probe)
            connection = _probe_connection(probe, inventory)
            for sensor in sensors:
                await context.helper.write_profile(connection, sensor, name, content)
                await sync_default_profile(context, connection, sensor, probe)
            refreshed.append(probe)
            await context.log(
                "jobs.iperf.profile_deployed",
                params={"probe": probe, "endpoint": name},
                target=probe,
            )
        except Exception as exc:
            # Named rather than raised: one unreachable probe must not undo a
            # rotation the endpoint has already accepted, and the probe that
            # missed it is exactly what an operator has to be told.
            await context.log(
                "jobs.iperf.probe_locked_out",
                level=LogLevel.WARNING,
                params={"probe": probe, "endpoint": name},
                target=probe,
                raw=str(exc),
            )
    return refreshed
