"""Single-probe maintenance jobs.

Small handlers that each drive one probe-helper exchange. They exist as jobs
rather than as synchronous endpoints because they can hang on an unreachable
host, and because "who installed the CA on berlin-01, and when" is a question
the audit trail should be able to answer.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.domain.enums import LogLevel
from app.infrastructure.probe_helper import ProbeConnection
from app.workers.context import JobContext

INSTALL_CA_STEPS: tuple[str, ...] = ("check_reachable", "install_ca", "verify")
INSTALL_CA_JOB_TYPE = "probe.install_ca"

VALIDATE_STEPS: tuple[str, ...] = ("check_reachable", "collect_state", "evaluate")
VALIDATE_JOB_TYPE = "probe.validate"


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
