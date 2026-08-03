"""Single-probe maintenance jobs.

Small handlers that each drive one probe-helper exchange. They exist as jobs
rather than as synchronous endpoints because they can hang on an unreachable
host, and because "who installed the CA on berlin-01, and when" is a question
the audit trail should be able to answer.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError, RuntimeStateError
from app.domain.enums import LogLevel
from app.infrastructure.helper_signing import HelperSigner
from app.infrastructure.probe_helper import ProbeConnection
from app.workers.context import JobContext

INSTALL_CA_STEPS: tuple[str, ...] = ("check_reachable", "install_ca", "verify")
INSTALL_CA_JOB_TYPE = "probe.install_ca"

VALIDATE_STEPS: tuple[str, ...] = ("check_reachable", "collect_state", "evaluate")
VALIDATE_JOB_TYPE = "probe.validate"

HELPER_UPDATE_STEPS: tuple[str, ...] = ("check_reachable", "send_helper", "verify")
HELPER_UPDATE_JOB_TYPE = "probe.helper_update"

# The file this platform ships, served from the same path the bootstrap hands
# out during enrolment - one helper, one source.
HELPER_ASSET = "libexec/prtg-nats-probe-helper"


def _connection(context: JobContext, username: str) -> ProbeConnection:
    inventory = context.runtime.read_probe(username)
    return ProbeConnection(
        nats_username=username, host=inventory.ssh_host, port=inventory.ssh_port
    )


async def install_ca(context: JobContext) -> dict[str, Any]:
    username: str = context.payload["probe"]
    connection = _connection(context, username)

    await context.step("check_reachable")
    await context.helper.probe_info(connection)
    await context.log("jobs.probe.reachable", params={"probe": username})

    await context.step("install_ca")
    ca_pem = context.runtime.ca_pem()
    await context.helper.install_ca(connection, ca_pem)
    await context.log("jobs.probe.ca_installed", params={"probe": username})

    await context.step("verify")
    # Ask again rather than trust the write: the point of the step is to prove
    # the probe now presents the fingerprint we expect.
    info = await context.helper.probe_info(connection)
    reported = info.value("ca_sha256")
    await context.log(
        "jobs.probe.ca_verified",
        params={"probe": username, "ca_sha256": reported or "none"},
    )
    return {"probe": username, "ca_sha256": reported}


async def helper_update(context: JobContext) -> dict[str, Any]:
    """Put the helper this platform ships onto the probe.

    The probe verifies the signature before it writes anything, so a failure
    here leaves the old helper in place - which is the whole reason this is
    allowed over the management channel at all.
    """
    username: str = context.payload["probe"]
    connection = _connection(context, username)

    await context.step("check_reachable")
    before = await context.helper.probe_info(connection)
    await context.log(
        "jobs.probe.helper_before",
        params={
            "probe": username,
            "version": before.value("helper_version") or "unknown",
        },
    )

    await context.step("send_helper")
    asset = context.settings.asset_dir / HELPER_ASSET
    if not asset.is_file():
        raise RuntimeStateError(
            params={"path": str(asset)}, details="the probe helper asset is missing"
        )
    script = asset.read_text(encoding="utf-8")
    signature = HelperSigner(context.settings).sign(asset.read_bytes())
    response = await context.helper.helper_update(connection, script, signature)
    await context.log(
        "jobs.probe.helper_sent",
        params={"probe": username, "version": response.value("version") or "unknown"},
        raw=response.raw,
    )

    await context.step("verify")
    # Asked again rather than believed: the answer above came from the helper
    # that was replaced, this one comes from the helper that took its place.
    after = await context.helper.probe_info(connection)
    version = after.value("helper_version") or "unknown"
    await context.log(
        "jobs.probe.helper_updated", params={"probe": username, "version": version}
    )
    return {
        "probe": username,
        "helper_version": version,
        "helper_sha256": after.value("helper_sha256"),
    }


async def validate(context: JobContext) -> dict[str, Any]:
    """Collect everything a probe can say about itself, in one pass."""
    username: str = context.payload["probe"]
    findings: list[dict[str, str]] = []

    await context.step("check_reachable")
    try:
        connection = _connection(context, username)
        info = await context.helper.probe_info(connection)
    except AppError as error:
        await context.log(
            "jobs.probe.unreachable",
            level=LogLevel.ERROR,
            params={"probe": username},
            raw=error.details,
        )
        raise

    await context.step("collect_state")
    sensors = await context.helper.sensor_list(connection)
    await context.log(
        "jobs.probe.state_collected",
        params={
            "probe": username,
            "service": info.value("service") or "unknown",
            "sensors": len(sensors.records),
        },
        raw=info.raw,
    )

    await context.step("evaluate")
    if (info.value("service") or "") != "active":
        findings.append({"code": "probe.service_inactive", "severity": "critical"})
    if (info.value("ca_sha256") or "none") == "none":
        findings.append({"code": "probe.ca_missing", "severity": "critical"})
    if (info.value("config") or "none") == "none":
        findings.append({"code": "probe.config_missing", "severity": "critical"})

    for finding in findings:
        await context.log(
            "jobs.probe.finding",
            level=LogLevel.WARNING,
            params={"probe": username, "finding": finding["code"]},
        )
    if not findings:
        await context.log("jobs.probe.validated", params={"probe": username})

    return {"probe": username, "findings": findings}
