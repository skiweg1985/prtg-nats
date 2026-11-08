"""Stand-ins for everything outside the platform, for the screenshot demo.

The platform's own logic runs unchanged. What is replaced is only what would
leave this machine: the SSH channel to probes and iperf endpoints, the PRTG
core's API, the Docker socket and the updater container. Each fake answers
the way the real counterpart does (the probe fake speaks the helper protocol
of libexec/prtg-nats-probe-helper), so the platform computes states, jobs
and deviations from those answers itself.

Nothing here may be imported by the product. It lives under the guide and
is started only by run.sh.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import ssl
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import world
from app.core.errors import ProbeUnreachableError
from app.infrastructure.known_hosts import HostKey
from app.infrastructure.probe_helper import (
    CURRENT_HELPER_VERSION,
    HelperCommand,
    HelperRequest,
    refusal_error,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

CONFIG_PATH = "/etc/paessler/mpprobe/config.yaml"
CA_PATH = "/usr/local/share/ca-certificates/prtg-nats-ca.crt"


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Probes and iperf endpoints ---------------------------------------------


class FakeWorld:
    """What every fake host currently holds, persisted beside the runtime.

    Persisted so a restarted backend finds the same sensors on the same probes
    it rolled out before - the platform's cache would otherwise disagree with
    the "hosts" on the first refresh.
    """

    def __init__(self, path: Path, helper_sha256: str) -> None:
        self._path = path
        self._helper_sha256 = helper_sha256
        self.state: dict[str, Any] = {"probes": {}, "endpoints": {}}
        if path.is_file():
            self.state = json.loads(path.read_text(encoding="utf-8"))
        # Requests that should fail or hang, keyed "probe/sensor". Set by the
        # seed to produce the failed and the running job; not persisted.
        self.failing_activations: dict[str, str] = {}
        self.hanging_activations: set[str] = set()
        for spec in world.PROBES:
            self.state["probes"].setdefault(spec.username, self._initial(spec))

    def save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _initial(spec: world.ProbeSpec) -> dict[str, Any]:
        sensors = {}
        for name, version in spec.preinstalled.items():
            # An install from an earlier release: its own script digest, and a
            # reserved interface where the sensor needs one.
            sensors[name] = {
                "version": version,
                "sha256": _sha(f"{name}-v{version}"),
                "helper_sha256": _sha(f"{name}-helper-v{version}")
                if name in ("wlan-auth", "link-quality")
                else "none",
                "helper": "active" if name in ("wlan-auth", "link-quality") else "none",
                "tool": None,
            }
        return {
            "reachable": True,
            "ca_sha256": None,
            "config": None,
            "channel_cert": None,
            "channel_cert_issued": None,
            "channel_service": "absent",
            "sensors": sensors,
            "staged": {},
            "reserved": {"wlan0": "wlan-auth"} if "wlan-auth" in sensors else {},
            "profiles": [],
            "overlay": None,
            "pending_csr_key": None,
        }

    def probe(self, username: str) -> dict[str, Any]:
        return self.state["probes"][username]

    def set_reachable(self, username: str, reachable: bool) -> None:
        self.probe(username)["reachable"] = reachable
        self.save()

    # --- The transport ------------------------------------------------------

    async def run(self, connection: Any, request: HelperRequest) -> str:
        label = connection.label
        if label in self.state["probes"]:
            return await self._probe_request(label, connection, request)
        return self._endpoint_request(label, connection, request)

    async def _probe_request(
        self, username: str, connection: Any, request: HelperRequest
    ) -> str:
        probe = self.probe(username)
        spec = world.PROBES_BY_NAME[username]
        if not probe["reachable"]:
            # The real transport waits for its connect timeout first; a short
            # pause keeps the job log's timing believable without stalling.
            await asyncio.sleep(1.5)
            raise ProbeUnreachableError(
                params={"probe": username},
                details=f"ssh: connect to host {connection.host} port 22: "
                "Connection timed out",
            )
        # About what one SSH round trip to a probe costs, so job durations
        # read like real ones.
        await asyncio.sleep(0.2)
        command = request.command
        args = request.arguments
        handler = getattr(self, "_p_" + command.value.replace("-", "_"), None)
        if handler is None:
            return f"OK {command.value}\n"
        result = handler(username, spec, probe, args, request.payload or "")
        if asyncio.iscoroutine(result):
            result = await result
        self.save()
        return str(result)

    # Each _p_ method answers one helper request the way the shipped helper
    # formats it.

    def _p_probe_info(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        lines = [
            "OK probe-info",
            f"package={spec.package}",
            "service=active",
            f"helper_version={CURRENT_HELPER_VERSION}",
            f"helper_sha256={self._helper_sha256}",
            f"platform={spec.platform}",
            f"hostname={spec.hostname}",
            f"ca_sha256={probe['ca_sha256'] or 'none'}",
            *self._overlay_lines(probe),
            f"channel_cert={probe['channel_cert'] or 'none'}",
            f"channel_cert_issued={probe['channel_cert_issued'] or 'none'}",
            f"channel_service={probe['channel_service']}",
            f"addresses={spec.site_address}",
        ]
        config = probe["config"]
        if config is None:
            lines.append("config=none")
        else:
            lines += [
                f"config={CONFIG_PATH}",
                f"id={config.get('id') or 'none'}",
                f"access_key={config.get('access_key') or 'none'}",
                f"name={config.get('name') or 'none'}",
            ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _overlay_lines(probe: dict[str, Any]) -> list[str]:
        overlay = probe.get("overlay")
        if not overlay:
            return [
                "overlay_mode=off",
                "overlay_public_key=none",
                "overlay_address=none",
                "overlay_endpoint=none",
                "overlay_interface_up=false",
                "overlay_handshake_age=none",
                "overlay_route_active=false",
                "overlay_direct_ok=unknown",
            ]
        route = overlay.get("route_active", False)
        return [
            f"overlay_mode={overlay['mode']}",
            f"overlay_public_key={overlay['public_key']}",
            f"overlay_address={overlay['address']}",
            f"overlay_endpoint={overlay['endpoint']}",
            "overlay_interface_up=true",
            f"overlay_handshake_age={secrets.randbelow(90) + 5}",
            f"overlay_route_active={'true' if route else 'false'}",
            f"overlay_direct_ok={'no' if route else 'yes'}",
        ]

    def _p_status(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK active config={CONFIG_PATH}\n"

    def _p_install_ca(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        certificate = x509.load_pem_x509_certificate(payload.encode("ascii"))
        der = certificate.public_bytes(serialization.Encoding.DER)
        probe["ca_sha256"] = hashlib.sha256(der).hexdigest()
        return f"OK ca-installed {CA_PATH}\n"

    def _p_channel_csr(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, username)])
            )
            .sign(key, hashes.SHA256())
        )
        pem = csr.public_bytes(serialization.Encoding.PEM)
        encoded = base64.b64encode(pem).decode("ascii")
        return f"OK channel-csr\ncsr={encoded}\n"

    def _p_channel_install(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        certificate = x509.load_pem_x509_certificates(payload.encode("ascii"))[0]
        probe["channel_cert"] = certificate.not_valid_after_utc.date().isoformat()
        probe["channel_cert_issued"] = certificate.not_valid_before_utc.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        probe["channel_service"] = "active"
        return (
            "OK channel-install\n"
            f"channel_cert={probe['channel_cert']}\n"
            "channel_service=active\n"
        )

    def _p_channel_service(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        probe["channel_service"] = "active"
        return "OK channel-service\nchannel_service=active\n"

    def _p_write_config(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        values = {}
        for key in ("id", "access_key", "name"):
            match = re.search(rf"^{key}:\s*(.+)$", payload, re.MULTILINE)
            if match:
                values[key] = match.group(1).strip().strip("'\"")
        probe["staged_config"] = values
        return f"OK staged-config {args[0] if args else ''}\n"

    def _p_activate(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        if probe.get("staged_config"):
            probe["config"] = probe.pop("staged_config")
        return f"OK activated {args[0] if args else ''}\n"

    def _p_commit(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK committed {args[0] if args else ''}\n"

    def _p_rollback(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        probe.pop("staged_config", None)
        return f"OK rolled-back {args[0] if args else ''}\n"

    def _p_helper_update(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return (
            f"OK helper-updated version={CURRENT_HELPER_VERSION} "
            f"sha256={self._helper_sha256}\n"
        )

    # --- Sensors ------------------------------------------------------------

    def _p_sensor_prepare(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return "OK sensor-prepared\n"

    def _p_sensor_stage(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        transaction, sensor, slot = args
        staged = probe["staged"].setdefault(transaction, {"sensor": sensor})
        staged[slot] = payload
        return f"OK sensor-staged {sensor} {slot}\n"

    def _p_sensor_tool_stage(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        transaction, sensor = args[0], args[1]
        fields = dict(
            line.split("=", 1) for line in payload.splitlines() if "=" in line
        )
        staged = probe["staged"].setdefault(transaction, {"sensor": sensor})
        staged["tool"] = {
            "tool": fields.get("tool"),
            "tool_version": fields.get("version"),
            "tool_platform": fields.get("platform"),
            "tool_sha256": fields.get("sha256"),
            "tool_source": "managed",
            "tool_path": (
                f"/opt/prtg-nats/tools/{fields.get('tool')}/{fields.get('version')}/"
                f"{fields.get('platform')}/{fields.get('tool')}"
            ),
            "tool_compatible": "yes",
        }
        return (
            f"OK sensor-tool-staged {sensor} {fields.get('tool')} "
            f"version={fields.get('version')} platform={fields.get('platform')} "
            f"sha256={fields.get('sha256')}\n"
        )

    async def _p_sensor_activate(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        transaction = args[0]
        staged = probe["staged"].get(transaction) or {}
        sensor = staged.get("sensor", "")
        key = f"{username}/{sensor}"
        if key in self.hanging_activations:
            # The self-test that does not come back: what keeps one job
            # running for the screenshots. The job's own timeout is far away.
            await asyncio.sleep(6 * 3600)
        if key in self.failing_activations:
            probe["staged"].pop(transaction, None)
            raise refusal_error(
                username, HelperCommand.SENSOR_ACTIVATE, self.failing_activations[key]
            )
        await asyncio.sleep(0.4)
        previous = probe["sensors"].get(sensor, {})
        probe["sensors"][sensor] = {
            "version": (staged.get("version") or "none").strip(),
            "sha256": _sha(staged["script"]) if "script" in staged else "none",
            "helper_sha256": _sha(staged["wrapper"]) if "wrapper" in staged else "none",
            "helper": "active" if "wrapper" in staged else "none",
            "tool": staged.get("tool") or previous.get("tool"),
        }
        return (
            f"OK sensor-activated {sensor}\n"
            f"self-test: {sensor} produced valid Script v2 JSON\n"
        )

    def _p_sensor_commit(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        probe["staged"].pop(args[0], None)
        return f"OK sensor-committed {args[0]}\n"

    def _p_sensor_rollback(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        probe["staged"].pop(args[0], None)
        return f"OK sensor-rolled-back {args[0]}\n"

    def _p_sensor_recover(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK sensor-recovered {args[0] if args else ''}\n"

    def _p_sensor_remove(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        sensor = args[0]
        probe["sensors"].pop(sensor, None)
        for interface, holder in list(probe["reserved"].items()):
            if holder == sensor:
                probe["reserved"].pop(interface)
        return f"OK sensor-removed {sensor}\n"

    def _p_sensor_list(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        lines = ["OK sensor-list"]
        for name, sensor in sorted(probe["sensors"].items()):
            interfaces = ",".join(
                interface
                for interface, holder in sorted(probe["reserved"].items())
                if holder == name
            )
            tool = sensor.get("tool")
            tool_fields = (
                "\t".join(f"{key}={value}" for key, value in tool.items())
                if tool
                else "tool=none\ttool_version=none\ttool_platform=none\t"
                "tool_sha256=none\ttool_source=none\ttool_path=none\t"
                "tool_compatible=none"
            )
            lines.append(
                f"{name}\tversion={sensor['version']}\tsha256={sensor['sha256']}"
                f"\tinterfaces={interfaces or 'none'}\thelper={sensor['helper']}"
                f"\thelper_sha256={sensor['helper_sha256']}\t{tool_fields}"
            )
        return "\n".join(lines) + "\n"

    def _p_wireless_interfaces(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        lines = ["OK wireless-interfaces"]
        for interface in ("wlan0", "wlan1"):
            holder = probe["reserved"].get(interface, "none")
            lines.append(
                f"{interface}\treserved={holder}\tdefault_route=no"
                f"\toperstate={'down' if holder != 'none' else 'up'}"
                f"\tnm_state={'unmanaged' if holder != 'none' else 'disconnected'}"
                "\tconnection=none"
            )
        return "\n".join(lines) + "\n"

    def _p_sensor_reserve_interface(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        sensor, interface = args
        probe["reserved"][interface] = sensor
        return f"OK interface-reserved {sensor} {interface}\n"

    def _p_sensor_release_interface(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        sensor, interface = args
        probe["reserved"].pop(interface, None)
        return f"OK interface-released {sensor} {interface}\n"

    def _p_sensor_write_profile(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        entry = f"{args[0]}/{args[1]}"
        if entry not in probe["profiles"]:
            probe["profiles"].append(entry)
        return f"OK profile-written {args[0]} {args[1]}\n"

    def _p_sensor_remove_profile(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        entry = f"{args[0]}/{args[1]}"
        if entry in probe["profiles"]:
            probe["profiles"].remove(entry)
        return f"OK profile-removed {args[0]} {args[1]}\n"

    def _p_sensor_write_file(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK file-written {args[0]} {args[1]} {args[2]}\n"

    def _p_sensor_remove_file(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK files-removed {args[0]} {args[1]}\n"

    # --- Overlay ------------------------------------------------------------

    def _p_overlay_configure(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        values = dict(
            line.split("=", 1) for line in payload.splitlines() if "=" in line
        )
        existing = probe.get("overlay") or {}
        public_key = existing.get("public_key") or base64.b64encode(
            secrets.token_bytes(32)
        ).decode("ascii")
        probe["overlay"] = {
            "mode": args[0],
            "address": values.get("ADDRESS", ""),
            "endpoint": values.get("ENDPOINT", ""),
            "public_key": public_key,
            "route_active": spec.overlay_route_active and args[0] != "off",
        }
        return (
            "OK overlay-configure\n"
            f"overlay_public_key={public_key}\n"
            + "\n".join(self._overlay_lines(probe))
            + "\n"
        )

    def _p_overlay_info(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return "OK overlay-info\n" + "\n".join(self._overlay_lines(probe)) + "\n"

    def _p_overlay_remove(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        probe["overlay"] = None
        return "OK overlay-removed\n"

    def _p_access_source(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        return f"OK access-source {args[0]}\n"

    def _p_mpp_uninstall(self, username, spec, probe, args, payload):  # type: ignore[no-untyped-def]
        count = len(probe["sensors"])
        probe["sensors"] = {}
        return f"OK mpp-uninstalled package=none sensors={count}\n"

    # --- iperf endpoints ----------------------------------------------------

    def _endpoint_request(
        self, name: str, connection: Any, request: HelperRequest
    ) -> str:
        endpoint = self.state["endpoints"].setdefault(
            name, {"public_key_pem": None, "port": 5201, "user": "prtg-probe"}
        )
        command = request.command
        if command is HelperCommand.ENDPOINT_INFO:
            return (
                "OK endpoint-info\n"
                "iperf3=3.18\n"
                f"service={'active' if endpoint['public_key_pem'] else 'inactive'}\n"
                f"port={endpoint['port']}\n"
                f"user={endpoint['user']}\n"
                f"peer={world.NATS_HOST_IP}\n"
            )
        if command is HelperCommand.ENDPOINT_SETUP:
            endpoint["user"] = request.arguments[0]
            endpoint["port"] = int(request.arguments[1])
            answer = "OK endpoint-setup\n"
            if endpoint["public_key_pem"] is None:
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                endpoint["public_key_pem"] = (
                    key.public_key()
                    .public_bytes(
                        serialization.Encoding.PEM,
                        serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                    .decode("ascii")
                )
                encoded = base64.b64encode(endpoint["public_key_pem"].encode()).decode()
                answer += f"public_key_b64={encoded}\n"
            self.save()
            return answer
        if command is HelperCommand.ENDPOINT_REMOVE:
            endpoint["public_key_pem"] = None
            self.save()
            return "OK endpoint-removed\n"
        return f"OK {command.value}\n"


class FakeHelperTransport:
    """Takes the place of SshHelperTransport, with the same constructor.

    The observer the platform hands in is called exactly as the real
    transport calls it, so the management route and adopted addresses are
    recorded by the platform's own code.
    """

    world: FakeWorld  # set by the launcher before the app starts

    def __init__(self, **kwargs: Any) -> None:
        self._on_answer = kwargs.get("on_answer")

    async def run(self, connection: Any, request: HelperRequest, timeout: int) -> str:
        output = await self.world.run(connection, request)
        if self._on_answer is not None:
            with contextlib.suppress(Exception):
                self._on_answer(connection, connection.host, request, output)
        return output


# --- SSH host keys ----------------------------------------------------------


def host_keys_for(host: str) -> list[str]:
    """The SSH host keys a fake host presents, the same on every call.

    Derived from the address so a repeated scan finds the keys that were
    pinned, the way a real host would.
    """
    seed = hashlib.sha256(f"demo-host-key:{host}".encode()).digest()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    line = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        .decode("ascii")
    )
    return [line]


async def scan_host_keys(host: str, port: int = 22) -> tuple[HostKey, ...]:
    return tuple(
        key for key in (HostKey.parse(line) for line in host_keys_for(host)) if key
    )


async def install_access(*, host: str, **kwargs: Any) -> Any:
    """Signing in to an endpoint to install the management access.

    The fake host has nothing to install into; the channel the platform uses
    afterwards is the fake transport, which accepts it from then on. The log
    lines are the ones the real sign-in reports.
    """
    from app.infrastructure.ssh_provisioning import ProvisioningLog

    await asyncio.sleep(0.5)
    log = ProvisioningLog()
    log.add(f"signed in to {host}")
    log.add("staged 5 files")
    log.add("iperf3 3.18 present, service prtg-iperf3 enabled")
    log.add("management access installed for prtg-nats-admin")
    return log


# --- PRTG core --------------------------------------------------------------


def prtg_transport() -> httpx.MockTransport:
    """The two reads of PRTG's API v2 the platform makes, answered locally."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/experimental/probes"):
            offset = int(request.url.params.get("offset", "0"))
            items = [dict(item, kind="probenode") for item in world.PRTG_OBJECTS]
            return httpx.Response(200, json=items[offset:])
        match = re.search(r"/probes/(\d+)$", path)
        if match:
            for item in world.PRTG_OBJECTS:
                if item["id"] == match.group(1):
                    return httpx.Response(200, json=dict(item, kind="probenode"))
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


# --- Docker -----------------------------------------------------------------


class FakeDocker:
    """The Docker capability, mapped onto the demo's own NATS container.

    The platform addresses its containers by fixed names. Here "prtg-nats"
    means the demo container and nothing else; the proxy and the overlay hub
    exist only as states. No other container on this machine is touched.
    """

    def __init__(self, nats_container: str, checkout: Path, runtime: Path) -> None:
        self.nats_container = nats_container
        self.checkout = checkout
        self.runtime = runtime
        self.started_at = _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.hub_running = False

    async def _docker(self, *args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return process.returncode or 0, output.decode("utf-8", "replace")

    def install(self) -> None:
        """Replace the adapter's socket calls with the mapping above."""
        from app.infrastructure import docker as module

        fake = self

        def is_nats(container: Any) -> bool:
            return container == module.StackContainer.NATS

        async def inspect(self: Any, container: Any) -> Any:
            if is_nats(container):
                code, output = await fake._docker(
                    "inspect", "--format", "{{json .State}}", fake.nats_container
                )
                if code != 0:
                    return module.ContainerState(name=container.value, exists=False)
                state = json.loads(output)
                return module.ContainerState(
                    name=container.value,
                    exists=True,
                    running=bool(state.get("Running")),
                    status=state.get("Status"),
                    # The demo container has no health check of its own; the
                    # stack's one asks /healthz, which is what this reports.
                    health="healthy" if state.get("Running") else "unhealthy",
                    image="nats:2.10.29-alpine",
                    started_at=state.get("StartedAt"),
                )
            return module.ContainerState(
                name=container.value,
                exists=True,
                running=True,
                status="running",
                health="healthy",
                image="caddy:2.9-alpine",
                started_at=fake.started_at,
            )

        async def restart(self: Any, container: Any, *, timeout: int = 30) -> None:
            if is_nats(container):
                await fake._docker("restart", "-t", "5", fake.nats_container)
            else:
                await asyncio.sleep(1)
                fake.started_at = _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        async def start(self: Any, container: Any) -> None:
            if is_nats(container):
                await fake._docker("start", fake.nats_container)

        async def stop(self: Any, container: Any, *, timeout: int = 30) -> None:
            if is_nats(container):
                await fake._docker("stop", "-t", "5", fake.nats_container)

        async def wait_healthy(self: Any, container: Any, **kwargs: Any) -> Any:
            for _ in range(20):
                state = await inspect(self, container)
                if state.running:
                    return state
                await asyncio.sleep(0.5)
            return state

        async def reload_config(self: Any, container: Any) -> None:
            if is_nats(container):
                await fake._docker("kill", "-s", "HUP", fake.nats_container)

        async def logs(self: Any, container: Any, *, tail: int = 200) -> str:
            if is_nats(container):
                _, output = await fake._docker(
                    "logs", "--tail", str(tail), fake.nats_container
                )
                return output
            return ""

        async def compose_project(self: Any) -> Any:
            # Shown on the update page as the installation directory; the
            # updater stand-in reads the real demo checkout instead.
            return module.ComposeProject(
                name=module.COMPOSE_PROJECT_NAME,
                working_dir=Path("/opt/prtg-nats"),
                config_file=Path("/opt/prtg-nats/compose.yaml"),
            )

        async def image_exists(self: Any, image: str) -> bool:
            return True

        async def runtime_mountpoint(self: Any) -> str:
            return str(fake.runtime)

        async def overlay_hub_running(self: Any) -> bool:
            return fake.hub_running

        async def create_overlay_hub(self: Any) -> str:
            return "demo-overlay-hub"

        async def start_container(self: Any, container_id: str) -> None:
            if container_id == "demo-overlay-hub":
                fake.hub_running = True

        async def remove_overlay_hub(self: Any) -> None:
            fake.hub_running = False

        async def run_updater(
            self: Any, command: Any, arguments: Any = (), **kwargs: Any
        ) -> Any:
            if command != module.UpdaterCommand.PROBE:
                return module.ContainerRun(
                    exit_code=1,
                    output="updating is not available in the screenshot demo\n",
                )
            return module.ContainerRun(
                exit_code=0, output=fake.probe_checkout(arguments)
            )

        async def read_volume_archive(self: Any, volume: str, target: Any) -> int:
            # The JetStream store of the demo container, as the tar stream the
            # Docker archive endpoint would send. The container is stopped
            # here (the backup does that first), and docker cp reads a
            # stopped container's filesystem just the same.
            process = await asyncio.create_subprocess_exec(
                "docker",
                "cp",
                f"{fake.nats_container}:/data",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            size = 0
            assert process.stdout is not None
            while chunk := await process.stdout.read(65536):
                target.write(chunk)
                size += len(chunk)
            await process.wait()
            if size == 0:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    info = tarfile.TarInfo("data/jetstream")
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                target.write(buffer.getvalue())
                size = len(buffer.getvalue())
            return size

        module.DockerAdapter.available = property(lambda self: True)  # type: ignore[method-assign,assignment]
        for name, function in {
            "inspect": inspect,
            "restart": restart,
            "start": start,
            "stop": stop,
            "wait_healthy": wait_healthy,
            "reload_config": reload_config,
            "logs": logs,
            "compose_project": compose_project,
            "image_exists": image_exists,
            "runtime_mountpoint": runtime_mountpoint,
            "overlay_hub_running": overlay_hub_running,
            "create_overlay_hub": create_overlay_hub,
            "start_container": start_container,
            "remove_overlay_hub": remove_overlay_hub,
            "run_updater": run_updater,
            "read_volume_archive": read_volume_archive,
        }.items():
            setattr(module.DockerAdapter, name, function)

    def probe_checkout(self, arguments: Any) -> str:
        """What the updater's probe command prints, from the demo checkout."""

        def git(*args: str) -> str:
            return subprocess.run(  # noqa: S603 - fixed argv, demo checkout
                ["git", "-C", str(self.checkout), *args],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

        branch = arguments[0] if arguments else git("rev-parse", "--abbrev-ref", "HEAD")
        git("fetch", "--quiet", "origin")
        head = git("rev-parse", "HEAD")
        remote = git("rev-parse", f"origin/{branch}")
        log = git("log", "--format=%H%x09%s%x09%cI", f"HEAD..origin/{branch}")
        commits = []
        for line in log.splitlines():
            sha, subject, date = line.split("\t", 2)
            commits.append({"sha": sha, "subject": subject, "date": date})
        payload = {
            "branch": branch,
            "head": head,
            "dirty": bool(git("status", "--porcelain")),
            "remote_head": remote,
            "reachable": bool(remote),
            "error": "",
            "commits": commits,
        }
        return json.dumps(payload) + "\n"


# --- Probes connected to NATS -----------------------------------------------


class NatsClients:
    """Keeps one NATS connection open per probe that should look connected.

    The plain client protocol over TLS, with each probe's own credentials
    from the runtime - so the server's connection list and its
    authorized_user fields are real, and the platform reads them as it would
    from MPP probes.
    """

    def __init__(
        self, runtime: Path, host: str, port: int, fake_world: FakeWorld
    ) -> None:
        self._runtime = runtime
        self._host = host
        self._port = port
        self._world = fake_world
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def wanted(self) -> list[str]:
        names = []
        for spec in world.PROBES:
            probe = self._world.state["probes"].get(spec.username)
            if probe and probe.get("reachable") and probe.get("config"):
                names.append(spec.username)
        return names

    async def run_forever(self) -> None:
        while True:
            for username in self.wanted():
                task = self._tasks.get(username)
                if task is None or task.done():
                    self._tasks[username] = asyncio.create_task(self._connect(username))
            for username, task in list(self._tasks.items()):
                if username not in self.wanted():
                    task.cancel()
                    self._tasks.pop(username)
            await asyncio.sleep(10)

    def _password(self, username: str) -> str | None:
        path = self._runtime / "credentials" / f"{username}.env"
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("NATS_PASSWORD="):
                return line.split("=", 1)[1].strip()
        return None

    async def _connect(self, username: str) -> None:
        password = self._password(username)
        if password is None:
            return
        context = ssl.create_default_context(
            cafile=str(self._runtime / "certs" / "ca.pem")
        )
        context.check_hostname = False
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
            await reader.readline()  # INFO, in clear text before the TLS upgrade
            await writer.start_tls(context)
            connect = {
                "user": username,
                "pass": password,
                "verbose": False,
                "pedantic": False,
                "tls_required": True,
                "name": f"prtgmpprobe {username}",
                "lang": "go",
                "version": "1.37.0",
                "protocol": 1,
            }
            writer.write(f"CONNECT {json.dumps(connect)}\r\nPING\r\n".encode())
            writer.write(b"SUB prtg.probe.commands 1\r\n")
            # The stream the results land in, and the consumer that reads
            # them. Created by whichever probe connects first; the others get
            # "already in use" back, which is harmless.
            for subject, request in (
                (
                    "$JS.API.STREAM.CREATE.PRTG_RESULTS",
                    {
                        "name": "PRTG_RESULTS",
                        "subjects": ["prtg.probe.results.>"],
                        "storage": "file",
                        "max_age": 7 * 24 * 3600 * 10**9,
                        "max_bytes": 512 * 1024 * 1024,
                    },
                ),
                (
                    "$JS.API.CONSUMER.DURABLE.CREATE.PRTG_RESULTS.prtg-core",
                    {
                        "stream_name": "PRTG_RESULTS",
                        "config": {
                            "durable_name": "prtg-core",
                            "ack_policy": "explicit",
                        },
                    },
                ),
            ):
                body = json.dumps(request)
                writer.write(
                    f"PUB {subject} _INBOX.demo {len(body)}\r\n{body}\r\n".encode()
                )
            await writer.drain()
            sent = 0
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=5)
                except TimeoutError:
                    line = b"\n"
                if line.startswith(b"PING"):
                    writer.write(b"PONG\r\n")
                elif line.startswith(b"-ERR") or line == b"" and reader.at_eof():
                    return
                # A little traffic, so the message counters move like a probe's
                # results do.
                body = json.dumps({"probe": username, "seq": sent})
                writer.write(
                    f"PUB prtg.probe.results.{username} {len(body)}\r\n{body}\r\n".encode()
                )
                sent += 1
                await writer.drain()
        except (OSError, ssl.SSLError, asyncio.IncompleteReadError):
            return


def helper_digest(asset_dir: Path) -> str:
    return hashlib.sha256(
        (asset_dir / "libexec" / "prtg-nats-probe-helper").read_bytes()
    ).hexdigest()


def env_flag(name: str) -> bool:
    return os.environ.get(name, "") not in ("", "0", "false")


__all__ = [
    "FakeDocker",
    "FakeHelperTransport",
    "FakeWorld",
    "NatsClients",
    "env_flag",
    "helper_digest",
    "host_keys_for",
    "install_access",
    "prtg_transport",
    "scan_host_keys",
]


# --- Stack verification -------------------------------------------------------


def install_verification(nats_port: int, runtime: Path) -> None:
    """Point the two network checks of the stack verification at the demo.

    On a real host they dial NATS_FQDN and the CA download on NATS_HOST_IP.
    Here those are documentation names that lead nowhere, so the login goes
    to the demo NATS container on loopback - still a real TLS login with the
    shared account, verified against the server name - and the CA download
    compares what the proxy would serve from runtime/public.
    """
    from app.services import verification

    async def nats_login(
        *, host: str, port: int, username: str, password: str, ca_path: str
    ) -> str:
        reader, writer = await asyncio.open_connection("127.0.0.1", nats_port)
        try:
            info = json.loads((await reader.readline())[5:])
            context = ssl.create_default_context(cafile=ca_path)
            await writer.start_tls(context, server_hostname=host)
            connect = {
                "user": username,
                "pass": password,
                "verbose": False,
                "pedantic": False,
                "name": "prtg-nats-web-verify",
                "lang": "python",
                "version": "0",
            }
            writer.write(b"CONNECT " + json.dumps(connect).encode() + b"\r\nPING\r\n")
            await writer.drain()
            reply = await reader.readline()
            if reply.strip() == b"PONG":
                return f"authenticated as {username}, server {info.get('version', '?')}"
            raise PermissionError(reply.decode(errors="replace").strip()[:120])
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()

    async def check_ca_endpoint(self: Any) -> Any:
        site = self._runtime.site_settings()
        url = f"http://{site.nats_host_ip}:{site.ca_http_port}/nats-ca.pem"
        served = runtime / "public" / "nats-ca.pem"
        active = runtime / "certs" / "ca.pem"
        if served.is_file() and served.read_bytes() == active.read_bytes():
            return verification.CheckResult("ca_endpoint", True, url)
        return verification.CheckResult(
            "ca_endpoint", False, "served CA differs from the active one"
        )

    verification._nats_login = nats_login  # type: ignore[assignment]
    verification.StackVerification._check_ca_endpoint = check_ca_endpoint  # type: ignore[method-assign]
