"""Finishing an iperf endpoint enrolment the host started.

The host has installed the restricted management access and reported its SSH
host keys. What is left is everything the platform owns: pinning those keys,
setting the endpoint up over the channel that was just installed, and recording
what it now holds.

The password is generated here and sent there, never the other way round. That
is the same decision the shell tooling wrote down years ago, and it survives
the move because the reason survives it: a password created on the endpoint
would have to travel back through a report, and this side needs it anyway to
hand to the probes.

The order matters. Nothing is recorded until the endpoint has actually been set
up - a record for an endpoint that does not answer is worse than no record,
because everything downstream believes it.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

from app.core.errors import RuntimeStateError
from app.domain.enums import LogLevel
from app.infrastructure import known_hosts
from app.infrastructure.iperf_helper import EndpointConnection
from app.workers.context import JobContext

ENROLL_STEPS: tuple[str, ...] = (
    "pin_host_key",
    "verify_access",
    "setup_endpoint",
    "write_record",
)
ENROLL_JOB_TYPE = "iperf.enroll"

# 24 bytes as hex, the same shape "openssl rand -hex 24" produced on the shell
# path. Long enough that the endpoint's rate limiting is not what protects it.
PASSWORD_BYTES = 24


async def enroll(context: JobContext) -> dict[str, Any]:
    payload = context.payload
    name: str = payload["name"]
    host: str = payload["host"]
    ssh_port: int = int(payload.get("ssh_port") or 22)
    iperf_port: int = int(payload.get("iperf_port") or 5201)
    username: str = payload.get("username") or "prtg-probe"
    reported_keys: list[str] = list(payload.get("host_keys") or [])

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

    # --- 2. Does the management channel answer? -----------------------------
    # The first use of the access the bootstrap installed. If the source
    # network in its authorized_keys names the wrong address, this is where it
    # shows - and the message says so, because the repair is a walk to that
    # host's console and nothing here can do it.
    await context.step("verify_access")
    connection = EndpointConnection(name=name, host=host, port=ssh_port)
    info = await context.endpoints.endpoint_info(connection)
    await context.log(
        "jobs.iperf.access_verified",
        params={
            "endpoint": name,
            "host": host,
            "iperf3": info.value("iperf3") or "not installed",
            "service": info.value("service") or "unknown",
        },
    )

    # What address the endpoint sees us arrive from. The platform cannot know
    # this for itself behind NAT, and it is what the "from=" rule has to name -
    # so it is recorded here, where a later invitation can be filled in with a
    # measured value instead of a guess.
    seen_from = info.value("peer")
    expected_from = payload.get("ssh_source_cidr")
    if seen_from and seen_from != "none":
        await context.log(
            "jobs.iperf.seen_from",
            params={
                "endpoint": name,
                "peer": seen_from,
                "allowed": expected_from or "—",
            },
        )

    # --- 3. The endpoint itself ---------------------------------------------
    # Deliberately over the channel rather than in the bootstrap: the channel
    # is what everything later depends on, so it is exercised before anything
    # relies on it. The same split the probe enrolment makes.
    await context.step("setup_endpoint")
    password = secrets.token_hex(PASSWORD_BYTES)
    response = await context.endpoints.endpoint_setup(
        connection, username=username, port=iperf_port, password=password
    )
    public_key_pem = _decode_public_key(response.value("public_key_b64"))
    await context.log(
        "jobs.iperf.endpoint_ready",
        params={
            "endpoint": name,
            "host": host,
            "port": str(iperf_port),
            "username": username,
        },
    )

    # --- 4. The record -------------------------------------------------------
    # Last, and the one step that cannot be retried on its own: the endpoint
    # already holds this password. A failure here is repaired by running the
    # enrolment again, which sets a fresh one - said out loud below, because
    # otherwise it looks like a lost endpoint rather than a repeated command.
    await context.step("write_record")
    try:
        context.runtime.write_iperf_record(
            name=name,
            host=host,
            port=iperf_port,
            username=username,
            password=password,
            public_key_pem=public_key_pem,
            managed=True,
        )
    except Exception:
        await context.log(
            "jobs.iperf.record_incomplete",
            level=LogLevel.WARNING,
            params={"endpoint": name, "host": host},
        )
        raise
    await context.log("jobs.iperf.enrolled", params={"endpoint": name, "host": host})

    return {
        "endpoint": name,
        "host": host,
        "port": iperf_port,
        "username": username,
        "public_key_stored": public_key_pem is not None,
        "seen_from": seen_from,
    }


def _decode_public_key(encoded: str | None) -> str | None:
    """The endpoint's public key, as the file it was.

    Absent is not an error here: the key pair is left alone by a repeated
    setup, so an endpoint that already had one answers without it. What is an
    error is something that decodes but is not a key - storing that would cost
    every probe its ability to encrypt the credentials it sends, and the
    failure would surface much later as a sensor nobody can explain.
    """
    if not encoded or encoded == "none":
        return None
    try:
        material = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeStateError(
            details="the endpoint returned a public key that is not readable"
        ) from exc
    if "-----BEGIN PUBLIC KEY-----" not in material:
        raise RuntimeStateError(
            details="the endpoint returned something that is not a public key"
        )
    return material
