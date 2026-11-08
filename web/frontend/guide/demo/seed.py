"""Fill the demo installation with three weeks of believable history.

Everything that the API can do is done through the API, as the account that
would have done it, so jobs, deployments, audit entries and the runtime files
are written by the platform itself. Two things it cannot do are written
directly, and only those:

* moving that history into the past - every row is created now and then
  shifted by the offset of the phase it was created in (``backdate``);
* one audit entry with the result "denied", which no route writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import world
from fakes import FakeDocker, FakeWorld, host_keys_for
from ulid import ULID

log = logging.getLogger("demo.seed")

TERMINAL = {"successful", "failed", "cancelled", "partially_successful"}


def _now() -> datetime:
    return datetime.now(UTC)


class Session:
    """One signed-in account, appearing to come from its own address."""

    def __init__(self, base_url: str, account: world.Account | None) -> None:
        headers = {}
        if account is not None:
            headers["X-Forwarded-For"] = account.source_ip
        self.account = account
        self.client = httpx.AsyncClient(
            base_url=base_url + "/api/v1", headers=headers, timeout=60
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def call(
        self, method: str, path: str, body: Any = None, *, ok: tuple[int, ...] = ()
    ) -> Any:
        response = await self.client.request(method, path, json=body)
        if response.status_code >= 400 and response.status_code not in ok:
            raise RuntimeError(
                f"{method} {path} -> {response.status_code}: {response.text[:600]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def login(self) -> None:
        assert self.account is not None
        await self.call(
            "POST",
            "/auth/login",
            {"username": self.account.username, "password": self.account.password},
        )

    async def job(self, accepted: Any, *, timeout: float = 120) -> dict[str, Any]:
        """Wait for a job the call above started, and return how it ended."""
        job_id = accepted["job_id"] if "job_id" in accepted else accepted["id"]
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            detail = await self.call("GET", f"/jobs/{job_id}")
            if detail["status"] in TERMINAL:
                if detail["status"] != "successful":
                    log.warning(
                        "job %s %s ended %s: %s",
                        detail["type"],
                        job_id,
                        detail["status"],
                        detail.get("error_details"),
                    )
                return dict(detail)
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError(f"job {job_id} did not finish")
            await asyncio.sleep(0.3)


class Timeline:
    """Which stretch of real time stands for which moment in the past."""

    def __init__(self) -> None:
        self.phases: list[tuple[datetime, timedelta]] = []

    @contextlib.contextmanager
    def at(self, days_ago: float, hour: int, minute: int = 0) -> Iterator[None]:
        target = (_now() - timedelta(days=days_ago)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        start = _now()
        self.phases.append((start, target - start))
        yield
        # A gap after each phase, so no row lands on a boundary.
        self.phases.append((_now(), timedelta(0)))

    @contextlib.contextmanager
    def ago(self, *, hours: int = 0, minutes: int = 0) -> Iterator[None]:
        target = _now() - timedelta(hours=hours, minutes=minutes)
        start = _now()
        self.phases.append((start, target - start))
        yield
        self.phases.append((_now(), timedelta(0)))

    def offset(self, moment: datetime) -> timedelta:
        chosen = timedelta(0)
        for start, offset in self.phases:
            if moment >= start:
                chosen = offset
        return chosen


async def wait_for_probe_jobs(api: Session) -> None:
    """Let the jobs the platform starts on its own settle first."""
    for _ in range(100):
        jobs = await api.call("GET", "/jobs?limit=50")
        if not any(job["status"] in ("queued", "running") for job in jobs):
            return
        await asyncio.sleep(0.3)


async def probe_ids(api: Session) -> dict[str, str]:
    return {
        probe["nats_username"]: probe["id"]
        for probe in await api.call("GET", "/probes")
    }


async def seed(
    base_url: str, state: Path, fake_world: FakeWorld, docker: FakeDocker
) -> None:
    timeline = Timeline()
    anonymous = Session(base_url, world.ACCOUNTS[0])
    admin_account, olga_account, viktor_account = world.ACCOUNTS
    admin = anonymous
    try:
        # --- Day one: the installation is set up -----------------------------
        with timeline.at(21, 9, 12):
            # What `prtg-nats setup` prints on the host. The demo shares the
            # API's process, so it issues the code the same way.
            from app.core.config import get_settings
            from app.services.setup_code import SetupCodeStore

            setup_code = SetupCodeStore(get_settings()).issue()
            await admin.call(
                "POST",
                "/auth/setup",
                {
                    "setup_code": setup_code,
                    "username": admin_account.username,
                    "password": admin_account.password,
                    "display_name": admin_account.display_name,
                },
            )
            await admin.job(await admin.call("POST", "/system/setup"), timeout=180)
            await admin.call(
                "PUT",
                "/prtg/settings",
                {"core_url": world.PRTG_CORE_URL, "token": world.PRTG_TOKEN},
            )

        with timeline.at(21, 9, 40):
            for account in (olga_account, viktor_account):
                await admin.call(
                    "POST",
                    "/users",
                    {
                        "username": account.username,
                        "password": account.password,
                        "display_name": account.display_name,
                        "email": f"{account.username}@example.test",
                        "roles": [account.role],
                        "must_change_password": False,
                    },
                )

        olga = Session(base_url, olga_account)
        viktor = Session(base_url, viktor_account)
        with timeline.at(21, 9, 55):
            await olga.login()
            await viktor.login()

        # The NATS server reads the accounts the setup job wrote; from here on
        # the probes have something to connect to once they are enrolled.
        await start_nats(state, docker)

        # --- The probes are enrolled, one invitation each ------------------
        for index, spec in enumerate(world.PROBES):
            with timeline.at(20 - index * 0.3, 10 + index, 5):
                await enroll_probe(base_url, admin, spec)
        await wait_for_probe_jobs(admin)
        ids = await probe_ids(admin)

        with timeline.at(19, 14, 20):
            for spec in world.PROBES:
                patch: dict[str, Any] = {}
                if spec.prtg_registered:
                    patch["prtg_registered"] = True
                if patch:
                    await olga.call("PATCH", f"/probes/{ids[spec.username]}", patch)
                if spec.prtg_object_id:
                    await olga.call(
                        "PUT",
                        f"/probes/{ids[spec.username]}/prtg-link",
                        {"object_id": spec.prtg_object_id},
                    )

        # --- iperf endpoints ----------------------------------------------
        with timeline.at(18, 11, 0):
            await admin.job(
                await admin.call(
                    "POST",
                    "/iperf-endpoints",
                    {
                        "name": world.IPERF_MANAGED["name"],
                        "host": world.IPERF_MANAGED["host"],
                        "ssh_source_cidr": f"{world.NATS_HOST_IP}/32",
                        "host_keys": host_keys_for(world.IPERF_MANAGED["host"]),
                        "admin": {"username": "admin", "password": "demo-endpoint-pw"},
                    },
                )
            )
            await admin.call(
                "POST",
                "/iperf-endpoints/register",
                {
                    "name": world.IPERF_EXTERNAL["name"],
                    "host": world.IPERF_EXTERNAL["host"],
                    "port": 5201,
                    # An endpoint somebody else runs, with the account and
                    # public key its operator handed over.
                    "username": "prtg-extern",
                    "password": "demo-extern-iperf-pw",
                    "public_key_pem": external_public_key(),
                },
            )

        # --- The overlay ------------------------------------------------------
        with timeline.at(17, 15, 30):
            await admin.call(
                "POST",
                "/overlay/enable",
                {
                    "endpoint_host": world.OVERLAY_ENDPOINT,
                    "default_mode": "auto",
                },
            )
            for spec in world.PROBES:
                if not spec.overlay_mode:
                    continue
                await admin.job(
                    await admin.call(
                        "POST",
                        "/overlay/peers",
                        {"probe_ids": [ids[spec.username]], "mode": spec.overlay_mode},
                    )
                )
            await admin.job(
                await admin.call(
                    "POST",
                    "/overlay/peers/refresh",
                    {
                        "probe_ids": [
                            ids[spec.username]
                            for spec in world.PROBES
                            if spec.overlay_mode
                        ]
                    },
                )
            )

        # --- Sensors roll out ---------------------------------------------------
        everyone = [ids[spec.username] for spec in world.PROBES]
        with timeline.at(16, 9, 30):
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {"sensor": "link-quality", "probe_ids": everyone},
                ),
                timeout=180,
            )
        with timeline.at(14, 10, 15):
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {
                        "sensor": "iperf-throughput",
                        "probe_ids": [
                            ids["probe-berlin-01"],
                            ids["probe-hamburg-01"],
                            ids["probe-leipzig-01"],
                        ],
                    },
                ),
                timeout=180,
            )
        # The first rollout hands every registered endpoint to a probe; each
        # site then keeps only what it measures against.
        with timeline.at(13, 16, 40):
            await olga.job(
                await olga.call(
                    "POST",
                    f"/iperf-endpoints/{world.IPERF_EXTERNAL['name']}/revoke",
                    {"probes": ["probe-berlin-01"]},
                )
            )
            await olga.job(
                await olga.call(
                    "POST",
                    f"/iperf-endpoints/{world.IPERF_MANAGED['name']}/revoke",
                    {"probes": ["probe-leipzig-01"]},
                )
            )

        # wlan-auth: two variants, the interface reserved, then the rollout.
        with timeline.at(12, 13, 45):
            wlan_probes = [ids["probe-berlin-01"], ids["probe-kiel-01"]]
            await admin.job(
                await admin.call(
                    "PUT",
                    "/sensors/wlan-auth/profiles/corporate",
                    {
                        "values": {
                            "AUTH": "peap",
                            "SSID": "Corporate",
                            "IDENTITY": "svc-wlan-monitor",
                            "PASSWORD": "demo-radius-pw",
                            "DOMAIN_SUFFIX_MATCH": "radius.example.test",
                            "NO_VERIFY_SERVER": "true",
                        },
                        "probes": wlan_probes + [ids["probe-hamburg-01"]],
                    },
                ),
            )
            await admin.job(
                await admin.call(
                    "PUT",
                    "/sensors/wlan-auth/profiles/lager",
                    {
                        "values": {
                            "AUTH": "psk",
                            "SSID": "Lager-WLAN",
                            "PSK": "demo-lager-psk",
                        },
                        "probes": wlan_probes,
                    },
                ),
            )
        with timeline.at(12, 14, 10):
            for probe_id in wlan_probes:
                await olga.job(
                    await olga.call(
                        "POST",
                        f"/probes/{probe_id}/sensors/wlan-auth/interfaces/wlan0/reserve",
                    )
                )
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {"sensor": "wlan-auth", "probe_ids": wlan_probes},
                ),
                timeout=180,
            )

        with timeline.at(11, 8, 50):
            await admin.job(
                await admin.call(
                    "PUT",
                    "/sensors/wlan-auth/profiles/gast",
                    {
                        "values": {
                            "AUTH": "psk",
                            "SSID": "Gast",
                            "PSK": "demo-guest-psk",
                        },
                        "probes": [ids["probe-kiel-01"]],
                    },
                ),
            )

        # A dry run before touching the Bremen line, then the real rollout.
        with timeline.at(9, 10, 0):
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {
                        "sensor": "aruba-uplink",
                        "probe_ids": [ids["probe-bremen-01"], ids["probe-kiel-01"]],
                        "dry_run": True,
                    },
                )
            )
        with timeline.at(9, 10, 20):
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {"sensor": "aruba-uplink", "probe_ids": [ids["probe-bremen-01"]]},
                )
            )

        # Kiel's gateway answers differently; the self-test fails there.
        with timeline.at(6, 15, 5):
            fake_world.failing_activations["probe-kiel-01/aruba-uplink"] = (
                "ERROR: self-test of aruba-uplink failed: the gateway at "
                "198.51.100.1 refused the API login (HTTP 401)\n"
                "previous state restored\n"
            )
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {"sensor": "aruba-uplink", "probe_ids": [ids["probe-kiel-01"]]},
                )
            )

        # internet-speed v1 -> v2: Bremen takes it, Leipzig's self-test fails.
        with timeline.at(3, 11, 30):
            fake_world.failing_activations["probe-leipzig-01/internet-speed"] = (
                "ERROR: self-test of internet-speed failed: no answer from the "
                "speedtest.net server list within 30 s\n"
                "previous state restored\n"
            )
            await olga.job(
                await olga.call(
                    "POST",
                    "/deployments",
                    {
                        "sensor": "internet-speed",
                        "probe_ids": [ids["probe-bremen-01"], ids["probe-leipzig-01"]],
                    },
                ),
                timeout=180,
            )

        # Maintenance: an export, a JetStream backup, a check of the stack.
        with timeline.at(2, 7, 0):
            await admin.job(await admin.call("POST", "/system/export"))
            await admin.job(await admin.call("POST", "/system/backup"), timeout=120)
            await admin.job(await admin.call("POST", "/system/verify"))
        with timeline.at(2, 7, 30):
            await admin.call("POST", "/credentials", {"username": world.SPARE_ACCOUNT})

        with timeline.at(1, 16, 40):
            await olga.job(
                await olga.call(
                    "POST", "/probes/actions/validate", {"probe_ids": everyone}
                ),
                timeout=180,
            )

        # The desired state, written once everything is where it should be.
        # Hamburg keeps the two sensors from the earlier release it had before
        # enrolment, which are what make it deviate.
        with timeline.at(1, 17, 5):
            await write_desired_states(olga, ids, fake_world)

        # --- Yesterday evening Leipzig dropped off the network ------------------
        fake_world.set_reachable("probe-leipzig-01", False)
        # Asked right away rather than at the end of the staleness window, so
        # the probe list says "unreachable" from the first page load.
        await admin.call(
            "POST", f"/probes/{ids['probe-leipzig-01']}/refresh", ok=(502,)
        )

        await sensor_logs(base_url)
        await audit_trail(base_url, olga, viktor, timeline)

        # This morning's sign-ins, so "last sign-in" reads like daily use.
        with timeline.ago(hours=4, minutes=20):
            await olga.login()
        with timeline.ago(hours=3, minutes=5):
            await viktor.login()
        with timeline.ago(minutes=50):
            await admin.login()
        await admin.call("POST", "/system/update/check")
        await olga.close()
        await viktor.close()
    finally:
        await anonymous.close()

    backdate(state / "runtime" / "web.db", timeline)
    backdate_files(state / "runtime" / "backups", timeline)
    backdate_mtimes(state / "runtime", timeline)
    log.warning("seed complete")


def external_public_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


async def enroll_probe(base_url: str, api: Session, spec: world.ProbeSpec) -> None:
    """Issue an invitation and answer it the way the bootstrap script does."""
    issued = await api.call(
        "POST",
        "/probes/enrollment/tokens",
        {
            "nats_username": spec.username,
            "probe_name": spec.probe_name,
            "expected_host": spec.site_address,
        },
    )
    async with httpx.AsyncClient(
        base_url=base_url + "/api/v1",
        headers={"X-Forwarded-For": spec.site_address},
        timeout=30,
    ) as host:
        response = await host.post(
            f"/enroll/{issued['token']}/callback",
            json={
                "hostname": spec.hostname,
                "ssh_port": 22,
                "host_keys": host_keys_for(spec.site_address),
                "access_installed": True,
                "package_installed": True,
            },
        )
        response.raise_for_status()
        await api.job(response.json(), timeout=120)


async def write_desired_states(
    api: Session, ids: dict[str, str], fake_world: FakeWorld
) -> None:
    for spec in world.PROBES:
        probe = fake_world.probe(spec.username)
        sensors = []
        for name in sorted(probe["sensors"]):
            entry: dict[str, Any] = {"name": name}
            if name == "wlan-auth":
                entry["interfaces"] = ["wlan0"]
            sensors.append(entry)
        await api.call(
            "PUT",
            f"/probes/{ids[spec.username]}/desired-state",
            {"sensors": sensors, "probe_name": spec.probe_name, "ca_required": True},
        )


async def start_nats(state: Path, docker: FakeDocker) -> None:
    """The demo NATS server, with the configuration the setup job rendered."""
    runtime = state / "runtime"
    ports = json.loads((state / "ports.json").read_text(encoding="utf-8"))
    subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "rm", "-f", docker.nats_container],  # noqa: S607
        capture_output=True,
        check=False,
    )
    result = subprocess.run(  # noqa: S603 - fixed argv
        [  # noqa: S607
            "docker",
            "run",
            "-d",
            "--name",
            docker.nats_container,
            "--label",
            "prtg-nats-guide-demo=1",
            "-p",
            f"127.0.0.1:{ports['nats']}:{world.NATS_PORT}",
            "-p",
            f"127.0.0.1:{ports['nats_monitor']}:8222",
            "-v",
            f"{runtime / 'conf'}:/etc/nats/conf:ro",
            "-v",
            f"{runtime / 'certs'}:/etc/nats/certs:ro",
            ports["nats_image"],
            "--config",
            "/etc/nats/conf/nats-server.conf",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not start NATS: {result.stderr.strip()}")
    await asyncio.sleep(2)


async def after_start(
    base_url: str, state: Path, fake_world: FakeWorld, docker: FakeDocker
) -> None:
    """What every start needs, seeded or not.

    The NATS container picks up the accounts written since it last read its
    configuration, and one rollout is left running for the job pages - a
    job that was running when the backend stopped is ended by the runner's
    own recovery, so it is started again on every start.
    """
    running = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "inspect", "-f", "{{.State.Running}}", docker.nats_container],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if running == "true":
        subprocess.run(  # noqa: S603 - fixed argv
            ["docker", "kill", "-s", "HUP", docker.nats_container],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        # stop.sh removes the container; its JetStream store is scratch.
        await start_nats(state, docker)
    olga = Session(base_url, world.ACCOUNTS[1])
    try:
        await olga.login()
        jobs = await olga.call("GET", "/jobs?limit=50")
        if any(job["status"] == "running" for job in jobs):
            return
        ids = await probe_ids(olga)
        # Another go at aruba-uplink after last week's failure; Bremen is done
        # within seconds, Kiel's self-test hangs.
        fake_world.hanging_activations.add("probe-kiel-01/aruba-uplink")
        await olga.call(
            "POST",
            "/deployments",
            {
                "sensor": "aruba-uplink",
                "probe_ids": [ids["probe-bremen-01"], ids["probe-kiel-01"]],
            },
        )
    finally:
        await olga.close()


# --- Sensor log ---------------------------------------------------------------


def _wlan_record(
    probe: str, network: str, moment: datetime, outcome: str, index: int
) -> dict[str, Any]:
    auth = {"Corporate": "peap", "Lager-WLAN": "psk", "Gast": "psk"}[network]
    profile = {"Corporate": "corporate", "Lager-WLAN": "lager", "Gast": "gast"}[network]
    octet = 20 + index % 200
    stamp = moment.strftime("%H:%M:%S")
    body: dict[str, Any] = {
        "ssid": network,
        "auth": auth,
        "interface": "wlan0",
        "stage": "dhcp",
        "identity": "svc-wlan-monitor" if auth == "peap" else "",
        "profile": profile,
        "bssid": f"02:00:5e:10:{index % 90 + 10:02x}:{(index * 7) % 250:02x}",
        "client_mac": "02:5e:9a:31:44:0" + str(index % 10),
        "client_mac_kind": "auto",
    }
    supplicant = [
        f"{stamp}.012 wlan0: SME: Trying to authenticate with {body['bssid']} (SSID='{network}' freq=5180 MHz)",
        f"{stamp}.031 wlan0: Trying to associate with {body['bssid']} (SSID='{network}' freq=5180 MHz)",
        f"{stamp}.048 wlan0: Associated with {body['bssid']}",
    ]
    if outcome == "ok":
        if auth == "peap":
            supplicant += [
                f"{stamp}.112 wlan0: CTRL-EVENT-EAP-STARTED EAP authentication started",
                f"{stamp}.140 wlan0: CTRL-EVENT-EAP-METHOD EAP vendor 0 method 25 (PEAP) selected",
                f"{stamp}.402 wlan0: CTRL-EVENT-EAP-SUCCESS EAP authentication completed successfully",
            ]
        supplicant += [
            f"{stamp}.455 wlan0: WPA: Key negotiation completed with {body['bssid']} [PTK=CCMP GTK=CCMP]",
            f"{stamp}.456 wlan0: CTRL-EVENT-CONNECTED - Connection to {body['bssid']} completed",
        ]
        body.update(
            {
                "ip_address": f"198.51.100.{octet}",
                "dhcp_server": "198.51.100.1",
                "gateway": "198.51.100.1",
                "gateway_kind": "router",
                "frequency_mhz": 5180,
                "signal_dbm": -52 - index % 17,
                "timings": {
                    "assoc_ms": 38 + index % 20,
                    "auth_ms": 290 + index % 140 if auth == "peap" else 60 + index % 30,
                    "dhcp_ms": 820 + index % 600,
                    "gateway_ms": 3 + index % 9,
                    "total_ms": 1400 + index % 700,
                },
                "supplicant": supplicant,
                "dhcp": [
                    "DHCPDISCOVER on wlan0 to 255.255.255.255 port 67",
                    f"DHCPOFFER of 198.51.100.{octet} from 198.51.100.1",
                    f"DHCPREQUEST for 198.51.100.{octet} on wlan0",
                    f"DHCPACK of 198.51.100.{octet} from 198.51.100.1",
                    f"bound to 198.51.100.{octet} -- renewal in 1800 seconds.",
                ],
                "events": ["interface wlan0 taken over", "interface wlan0 handed back"],
            }
        )
        code, summary, result = "ok", "OK", "ok"
    elif outcome == "eap":
        supplicant += [
            f"{stamp}.110 wlan0: CTRL-EVENT-EAP-STARTED EAP authentication started",
            f"{stamp}.138 wlan0: CTRL-EVENT-EAP-METHOD EAP vendor 0 method 25 (PEAP) selected",
            f"{stamp}.402 wlan0: EAP-MSCHAPV2: Authentication failed - E=691 R=1 C=0e2a V=3 M=FAILED",
            f"{stamp}.403 wlan0: CTRL-EVENT-EAP-FAILURE EAP authentication failed",
            f"{stamp}.404 wlan0: CTRL-EVENT-DISCONNECTED bssid={body['bssid']} reason=23",
        ]
        body.update(
            {
                "stage": "dhcp",
                "frequency_mhz": 5180,
                "signal_dbm": -61,
                "timings": {"assoc_ms": 41, "auth_ms": 355, "total_ms": 412},
                "supplicant": supplicant,
                "dhcp": [],
                "events": ["interface wlan0 taken over", "interface wlan0 handed back"],
            }
        )
        code, summary, result = (
            "auth-failed-eap",
            "credentials rejected by RADIUS",
            "error",
        )
    else:
        body.update(
            {
                "frequency_mhz": 2437,
                "signal_dbm": -74,
                "timings": {
                    "assoc_ms": 52,
                    "auth_ms": 71,
                    "dhcp_ms": 15000,
                    "total_ms": 15140,
                },
                "supplicant": supplicant
                + [
                    f"{stamp}.140 wlan0: WPA: Key negotiation completed with {body['bssid']} [PTK=CCMP GTK=CCMP]",
                    f"{stamp}.141 wlan0: CTRL-EVENT-CONNECTED - Connection to {body['bssid']} completed",
                ],
                "dhcp": [
                    "DHCPDISCOVER on wlan0 to 255.255.255.255 port 67 interval 3",
                    "DHCPDISCOVER on wlan0 to 255.255.255.255 port 67 interval 6",
                    "DHCPDISCOVER on wlan0 to 255.255.255.255 port 67 interval 6",
                    "No DHCPOFFERS received.",
                ],
                "events": ["interface wlan0 taken over", "interface wlan0 handed back"],
            }
        )
        code, summary, result = "dhcp-timeout", "no DHCP answer within 15 s", "error"
    return {
        "id": str(uuid.uuid4()),
        "sensor": "wlan-auth",
        "job": f"{abs(hash((probe, network))) % 16**12:012x}",
        "time": moment.isoformat(timespec="seconds"),
        "result": result,
        "code": code,
        "summary": summary,
        "body": body,
    }


async def sensor_logs(base_url: str) -> None:
    """A day of wlan-auth runs, sent the way each probe's channel sends them."""
    now = _now()
    plan = {
        "probe-berlin-01": ("Corporate", "Lager-WLAN"),
        "probe-kiel-01": ("Corporate", "Lager-WLAN", "Gast"),
        "probe-hamburg-01": ("Corporate",),
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        for probe, networks in plan.items():
            records = []
            index = 0
            for hours_ago in range(23, -1, -1):
                for minute in (7, 37):
                    moment = now - timedelta(hours=hours_ago, minutes=minute)
                    for network in networks:
                        index += 1
                        outcome = "ok"
                        if (
                            probe == "probe-kiel-01"
                            and network == "Gast"
                            and hours_ago in (4, 5)
                        ):
                            outcome = "dhcp"
                        if probe == "probe-hamburg-01" and hours_ago in (2, 9, 10):
                            outcome = "eap"
                        if (
                            probe == "probe-berlin-01"
                            and network == "Corporate"
                            and hours_ago == 15
                            and minute == 7
                        ):
                            outcome = "eap"
                        # Kiel's channel was out of reach for a while in the
                        # night and its buffer overflowed: those runs never
                        # arrived, the gap record below says so.
                        if probe == "probe-kiel-01" and hours_ago in (12, 13):
                            continue
                        records.append(
                            _wlan_record(probe, network, moment, outcome, index)
                        )
            if probe == "probe-kiel-01":
                start = now - timedelta(hours=13, minutes=40)
                until = now - timedelta(hours=12, minutes=7)
                records.append(
                    {
                        "id": str(uuid.uuid4()),
                        "sensor": "return-channel",
                        "job": None,
                        "time": until.isoformat(timespec="seconds"),
                        "result": "gap",
                        "code": "buffer_full",
                        "summary": "12 record(s) dropped because the buffer on the probe was full",
                        "body": {
                            "dropped": 12,
                            "from": start.isoformat(timespec="seconds"),
                            "until": until.isoformat(timespec="seconds"),
                        },
                    }
                )
            records.sort(key=lambda record: record["time"])
            response = await client.post(
                "/probe/v1/records",
                json={
                    "records": records,
                    "buffer": {"bytes": 2048, "limit": 20 * 1024 * 1024},
                },
                headers={"X-Probe-Identity": f"CN={probe}"},
            )
            response.raise_for_status()


# --- Audit trail --------------------------------------------------------------


async def audit_trail(
    base_url: str, olga: Session, viktor: Session, timeline: Timeline
) -> None:
    """Sign-ins over the last days, including a mistyped password."""
    for days, session in ((5, viktor), (4, olga), (3, viktor), (2, olga)):
        with timeline.at(days, 8, 15 + days):
            await session.login()
    with timeline.at(1, 7, 58):
        wrong = Session(base_url, world.ACCOUNTS[2])
        await wrong.call(
            "POST",
            "/auth/login",
            {"username": "viktor", "password": "not-the-password"},
            ok=(401, 403),
        )
        await wrong.close()
        await viktor.login()


# --- Moving history into the past -----------------------------------------


# Rows that describe the present: what the probes last said, the alerts
# derived from it, sessions with their expiry, the update check.
_PRESENT_TABLES = {
    "probe_observed_state",
    "probe_management_route",
    "probe_return_channel",
    "alert",
    "web_session",
    "stack_version",
    "alembic_version",
    "login_attempt",
}
_NEVER_SHIFTED = {"locked_until", "last_seen_at", "observed_at"}
# Columns that move by the phase of their own value rather than the row's: a
# sign-in this morning on an account created three weeks ago.
_SELF_ANCHORED = {"last_login_at"}


def backdate(database: Path, timeline: Timeline) -> None:
    """Shift every row by the offset of the phase it was written in.

    Child rows move with their parent - a job's steps and events with the job,
    a deployment's targets with the deployment - so a job never starts before
    it was created.
    """
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        offsets: dict[tuple[str, str], timedelta] = {}
        # The audit trail refuses updates by trigger, which is right for a
        # real installation. Moving the demo's history is the one exception:
        # the trigger is lifted for this transaction and put back unchanged.
        triggers = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name = 'audit_event'"
        ).fetchall()
        for trigger in triggers:
            connection.execute(f"DROP TRIGGER {trigger['name']}")

        def parse(value: Any) -> datetime | None:
            if not isinstance(value, str) or len(value) < 19:
                return None
            try:
                moment = datetime.fromisoformat(value)
            except ValueError:
                return None
            return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment

        def columns(table: str) -> list[str]:
            return [
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")  # noqa: S608
                if "DATETIME" in (row["type"] or "").upper()
                and row["name"] not in _NEVER_SHIFTED
            ]

        parents = {
            "job_step": "job",
            "job_event": "job",
            "deployment_target": "deployment",
        }
        foreign = {
            "job_step": "job_id",
            "job_event": "job_id",
            "deployment_target": "deployment_id",
        }
        ordered = [t for t in tables if t not in parents] + [
            t for t in tables if t in parents
        ]
        for table in ordered:
            if table in _PRESENT_TABLES:
                continue
            datetime_columns = columns(table)
            if not datetime_columns:
                continue
            anchor = next(
                (
                    c
                    for c in ("created_at", "ts", "started_at")
                    if c in datetime_columns
                ),
                datetime_columns[0],
            )
            rows = connection.execute(
                f"SELECT rowid AS _rowid, * FROM {table}"  # noqa: S608
            ).fetchall()
            for row in rows:
                if table in parents:
                    offset = offsets.get((parents[table], row[foreign[table]]))
                    if offset is None:
                        continue
                else:
                    moment = parse(row[anchor])
                    if moment is None:
                        continue
                    offset = timeline.offset(moment)
                if "id" in row:
                    offsets[(table, row["id"])] = offset
                if not offset:
                    continue
                updates = {}
                for column in datetime_columns:
                    moment = parse(row[column])
                    if moment is not None:
                        shift = (
                            timeline.offset(moment)
                            if column in _SELF_ANCHORED
                            else offset
                        )
                        updates[column] = (
                            (moment + shift)
                            .astimezone(UTC)
                            .strftime("%Y-%m-%d %H:%M:%S.%f")
                        )
                if table == "audit_event" and "ts" in updates:
                    # The audit list is ordered by id, and a ULID carries its
                    # moment; the id has to move with the time.
                    updates["id"] = str(
                        ULID.from_datetime(
                            datetime.fromisoformat(updates["ts"]).replace(tzinfo=UTC)
                        )
                    )
                if updates:
                    assignments = ", ".join(f"{column} = ?" for column in updates)
                    connection.execute(
                        f"UPDATE {table} SET {assignments} WHERE rowid = ?",  # noqa: S608
                        [*updates.values(), row["_rowid"]],
                    )
        _denied_entry(connection)
        for trigger in triggers:
            connection.execute(trigger["sql"])
        connection.commit()
    finally:
        connection.close()


def backdate_files(directory: Path, timeline: Timeline) -> None:
    """The archives on the maintenance page, moved with the jobs that wrote
    them: listed by file time, and named after the moment they were made."""
    import os
    import re

    for archive in sorted(directory.glob("*.tar.gz")):
        moment = datetime.fromtimestamp(archive.stat().st_mtime, UTC)
        shifted = moment + timeline.offset(moment)
        stamp = shifted.strftime("%Y%m%dT%H%M%SZ")
        renamed = archive.with_name(re.sub(r"\d{8}T\d{6}Z", stamp, archive.name))
        checksum = Path(f"{archive}.sha256")
        archive.rename(renamed)
        targets = [renamed]
        if checksum.is_file():
            digest = checksum.read_text(encoding="utf-8").split(" ", 1)[0]
            new_checksum = Path(f"{renamed}.sha256")
            checksum.unlink()
            new_checksum.write_text(f"{digest}  {renamed.name}\n", encoding="utf-8")
            new_checksum.chmod(0o600)
            targets.append(new_checksum)
        for path in targets:
            os.utime(path, (shifted.timestamp(), shifted.timestamp()))


def backdate_mtimes(runtime: Path, timeline: Timeline) -> None:
    """Runtime files the interface dates by their modification time - the
    variants, the endpoint records - moved with the phase that wrote them."""
    import os

    for path in runtime.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name.startswith("web.db") or "backups" in path.parts:
            continue
        moment = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        offset = timeline.offset(moment)
        if offset:
            shifted = (moment + offset).timestamp()
            os.utime(path, (shifted, shifted))


def _denied_entry(connection: sqlite3.Connection) -> None:
    """The viewer trying a rollout - refused, as the role says.

    No route writes a "denied" entry (the permission check raises before an
    audit record exists), so this one row is written by hand, in the shape
    AuditWriter.record produces.
    """
    actor = connection.execute(
        "SELECT id FROM web_user WHERE username = 'viktor'"
    ).fetchone()
    moment = (_now() - timedelta(days=1)).replace(hour=8, minute=4, second=11)
    connection.execute(
        "INSERT INTO audit_event (id, ts, actor_id, actor_name, source_ip, action, "
        "object_type, object_id, object_label, before_state, after_state, result, "
        "error_code, job_id, correlation_id, comment) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, NULL)",
        (
            str(ULID.from_datetime(moment)),
            moment.strftime("%Y-%m-%d %H:%M:%S.%f"),
            actor[0] if actor else None,
            "viktor",
            world.VIEWER_SOURCE,
            "deployment.create",
            "sensor",
            "wlan-auth",
            "wlan-auth",
            "denied",
            "auth.permission_denied",
            uuid.uuid4().hex,
        ),
    )
