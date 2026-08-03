"""Render the MPP configuration, natively.

Replaces the rendering half of the retired mpp-config.sh. The template stays
config/mpprobe-config.yaml.template with its @@PLACEHOLDER@@ markers, and the
validation rules are the same patterns - a value the shell would have refused
is refused here too.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import RuntimeStateError, ValidationFailedError

PROBE_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
ACCESS_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
PROBE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
NATS_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")
CLIENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

PLACEHOLDERS = (
    "PROBE_ID",
    "ACCESS_KEY",
    "PROBE_NAME",
    "NATS_HOST",
    "NATS_PORT",
    "NATS_USER",
    "NATS_PASSWORD",
    "SERVER_CA",
    "CLIENT_NAME",
)

DEFAULT_CLIENT_NAME = "prtgmpprobe"
DEFAULT_CA_PATH = "/etc/paessler/mpprobe/certs/nats-docker-ca.pem"


@dataclass(frozen=True, slots=True)
class ProbeConfigValues:
    probe_id: str
    access_key: str
    probe_name: str
    nats_host: str
    nats_port: int
    nats_user: str
    nats_password: str
    server_ca: str = DEFAULT_CA_PATH
    client_name: str = DEFAULT_CLIENT_NAME

    def validate(self) -> None:
        problems: list[str] = []
        if not PROBE_ID_PATTERN.match(self.probe_id):
            problems.append("probe_id")
        if not ACCESS_KEY_PATTERN.match(self.access_key):
            problems.append("access_key")
        if not PROBE_NAME_PATTERN.match(self.probe_name):
            problems.append("probe_name")
        if not NATS_HOST_PATTERN.match(self.nats_host):
            problems.append("nats_host")
        if not 1 <= self.nats_port <= 65535:
            problems.append("nats_port")
        if not CLIENT_NAME_PATTERN.match(self.client_name):
            problems.append("client_name")
        if not self.nats_password:
            problems.append("nats_password")
        if problems:
            raise ValidationFailedError(
                fields=problems, details="probe configuration values failed validation"
            )


def host_label(host: str) -> str:
    """The short, YAML- and PRTG-safe host part.

    A hostname keeps the part before the first dot. An IP address keeps every
    octet - 192.0.2.18 must not become "192", which would distinguish
    nothing in PRTG.
    """
    if re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$", host):
        host = host.replace(".", "-")
    else:
        host = host.split(".")[0]
    host = host.lower()
    host = re.sub(r"[^a-z0-9-]", "-", host).strip("-")
    return host or "probe"


def default_probe_name(host: str) -> str:
    return f"multi-platform-probe@{host_label(host)}"


def default_access_key(probe_name: str) -> str:
    """Readable part first, random part in full.

    In PRTG every access key sits under the next in one list; the start of the
    line is what a skimming eye sees. The random UUID carries the security, the
    label only the association.
    """
    label = host_label(probe_name.split("@")[-1])[:32]
    return f"{label}-{uuid.uuid4()}"


def generate_probe_id() -> str:
    return str(uuid.uuid4())


def render_probe_config(template_path: Path, values: ProbeConfigValues) -> str:
    """Fill the template. Every placeholder must resolve and none may remain -
    a half-rendered configuration is worse than a refusal."""
    values.validate()
    if not template_path.is_file():
        raise RuntimeStateError(
            params={"path": str(template_path)},
            details="mpprobe-config.yaml.template is missing",
        )

    mapping = {
        "PROBE_ID": values.probe_id,
        "ACCESS_KEY": values.access_key,
        "PROBE_NAME": values.probe_name,
        "NATS_HOST": values.nats_host,
        "NATS_PORT": str(values.nats_port),
        "NATS_USER": values.nats_user,
        "NATS_PASSWORD": values.nats_password,
        "SERVER_CA": values.server_ca,
        "CLIENT_NAME": values.client_name,
    }

    rendered = template_path.read_text(encoding="utf-8")
    for name, value in mapping.items():
        rendered = rendered.replace(f"@@{name}@@", value)

    leftover = re.findall(r"@@([A-Z_]+)@@", rendered)
    if leftover:
        raise RuntimeStateError(
            params={"placeholders": leftover},
            details="the configuration template contains unknown placeholders",
        )
    return rendered


def new_transaction_id() -> str:
    """A token the probe helper accepts as a transaction name."""
    return f"web-{secrets.token_hex(8)}"
