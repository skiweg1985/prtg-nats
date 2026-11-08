"""The fictional installation the guide's screenshots show.

Every name and address here is from a documentation range (RFC 5737) or the
.test top-level domain, so nothing in a screenshot can point at a real host.
The passwords are test values for this local demo instance only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NATS_FQDN = "nats.example.test"
NATS_HOST_IP = "192.0.2.10"
NATS_PORT = 23561
PRTG_CORE_URL = "https://prtg.example.test:1616"
PRTG_TOKEN = "demo-prtg-api-token-0000000000000000"  # noqa: S105 - demo value
CA_ORGANIZATION = "Example Logistik GmbH"

# Where the accounts appear to sign in from. The audit trail shows these,
# so they are documentation addresses rather than the loopback the seed
# really talks over.
ADMIN_SOURCE = "192.0.2.50"
OPERATOR_SOURCE = "198.51.100.60"
VIEWER_SOURCE = "198.51.100.61"


@dataclass(frozen=True)
class Account:
    username: str
    display_name: str
    role: str
    password: str
    source_ip: str


ACCOUNTS = (
    Account("admin", "Alex Admin", "administrator", "demo-admin-2026!", ADMIN_SOURCE),
    Account("olga", "Olga Operator", "operator", "demo-olga-2026!", OPERATOR_SOURCE),
    Account("viktor", "Viktor Viewer", "viewer", "demo-viktor-2026!", VIEWER_SOURCE),
)


@dataclass
class ProbeSpec:
    """One probe of the demo world and how it should look."""

    username: str
    probe_name: str
    site_address: str
    platform: str
    hostname: str
    package: str
    # PRTG: the object id it is linked to, and whether the operator ticked
    # "entered in PRTG". The name PRTG reports for the object may differ from
    # the enrolment name, which the interface points out.
    prtg_object_id: str | None
    prtg_registered: bool
    # Sensors already on the host before it was enrolled, by version. These
    # stand for installs from an earlier release.
    preinstalled: dict[str, str] = field(default_factory=dict)
    overlay_mode: str | None = None
    # What the overlay reports as the path NATS takes: True for the tunnel.
    overlay_route_active: bool = False
    reachable_at_end: bool = True


PROBES = (
    ProbeSpec(
        username="probe-berlin-01",
        probe_name="Berlin-Zentrale",
        site_address="198.51.100.11",
        platform="linux-amd64-glibc",
        hostname="mpp-berlin-01.example.test",
        package="25.4.112.1203",
        prtg_object_id="2041",
        prtg_registered=True,
        overlay_mode="auto",
        overlay_route_active=False,
    ),
    ProbeSpec(
        username="probe-hamburg-01",
        probe_name="Hamburg-Hafen",
        site_address="198.51.100.21",
        platform="linux-arm64-glibc",
        hostname="mpp-hamburg-01.example.test",
        package="25.4.112.1203",
        prtg_object_id=None,
        prtg_registered=True,
        # Two sensors from an earlier release: the two deviations.
        preinstalled={"wlan-auth": "15", "internet-speed": "1"},
        overlay_mode="on",
        overlay_route_active=True,
    ),
    ProbeSpec(
        username="probe-leipzig-01",
        probe_name="Leipzig-Lager",
        site_address="198.51.100.31",
        platform="linux-arm64-glibc",
        hostname="mpp-leipzig-01.example.test",
        package="25.3.110.1157",
        prtg_object_id="2043",
        prtg_registered=True,
        preinstalled={"internet-speed": "1"},
        overlay_mode="auto",
        overlay_route_active=True,
        reachable_at_end=False,
    ),
    ProbeSpec(
        username="probe-bremen-01",
        probe_name="Bremen-Werk",
        site_address="198.51.100.41",
        platform="linux-amd64-glibc",
        hostname="mpp-bremen-01.example.test",
        package="25.4.112.1203",
        prtg_object_id=None,
        prtg_registered=False,
        preinstalled={"internet-speed": "1"},
    ),
    ProbeSpec(
        username="probe-kiel-01",
        probe_name="Kiel-Aussenstelle",
        site_address="198.51.100.51",
        platform="linux-arm64-glibc",
        hostname="mpp-kiel-01.example.test",
        package="25.4.112.1203",
        prtg_object_id="2045",
        prtg_registered=True,
    ),
)

PROBES_BY_NAME = {probe.username: probe for probe in PROBES}

# The probe objects the faked PRTG core lists. Hamburg is not linked yet, but
# PRTG has an object under its enrolment name, so the link dialog finds it.
PRTG_OBJECTS = (
    {"id": "1", "name": "Local Probe", "status": "up"},
    {"id": "2041", "name": "Berlin-Zentrale", "status": "up"},
    {"id": "2042", "name": "Hamburg-Hafen", "status": "up"},
    {"id": "2043", "name": "Leipzig-Lager", "status": "down"},
    {"id": "2045", "name": "Kiel Außenstelle (MPP)", "status": "up"},
)

IPERF_MANAGED = {
    "name": "iperf-zentrale",
    "host": "203.0.113.20",
    "hostname": "iperf-zentrale.example.test",
}
IPERF_EXTERNAL = {
    "name": "iperf-extern",
    "host": "203.0.113.50",
}

OVERLAY_ENDPOINT = "overlay.example.test"

# The NATS account created by hand, not belonging to any probe.
SPARE_ACCOUNT = "prtg-reporting"

WLAN_NETWORKS = ("Corporate", "Lager-WLAN", "Gast")
