"""Verify the stack, natively.

Replaces the retired verify.sh and smoke-test.sh. The checks are the same
ones, ending in the one that matters: an authenticated login over TLS, spoken
directly in the NATS wire protocol - INFO, TLS upgrade, CONNECT, PING - the
exact path every probe takes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from dataclasses import dataclass

import httpx

from app.core.config import Settings
from app.domain.enums import CertificateKind, CertificateStatus
from app.infrastructure import helper_signing
from app.infrastructure.certificates import read_certificate
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.overlay import OverlayRuntime
from app.infrastructure.runtime_files import RuntimeFileStore


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class StackVerification:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime = RuntimeFileStore(settings)

    async def run(self, *, live: bool) -> list[CheckResult]:
        """Offline checks always; the live ones only when asked.

        Never raises: a verification that crashes on the fault it was meant to
        report is useless. Every problem becomes a failed check with a detail.
        """
        results = [
            self._check_runtime_complete(),
            self._check_certificates(),
            self._check_permissions(),
            self._check_server_config_renders(),
            self._check_overlay(),
        ]
        if live:
            results.append(await self._check_health_endpoint())
            results.append(await self._check_ca_endpoint())
            results.append(await self._check_authenticated_login())
        return results

    # --- Offline ------------------------------------------------------------

    def _check_runtime_complete(self) -> CheckResult:
        health = self._runtime.health()
        return CheckResult(
            name="runtime_complete",
            ok=health.state == "complete",
            detail=f"state={health.state}"
            + (f", missing: {', '.join(health.missing)}" if health.missing else ""),
        )

    def _check_certificates(self) -> CheckResult:
        site = self._runtime.site_settings()
        server = read_certificate(
            self._settings.cert_dir / "server.pem",
            CertificateKind.SERVER,
            key_path=self._settings.cert_dir / "server-key.pem",
        )
        if server.status is CertificateStatus.MISSING:
            return CheckResult("certificates", False, "server certificate is missing")
        if server.key_matches is False:
            return CheckResult(
                "certificates", False, "certificate and key do not match"
            )
        if site.nats_fqdn and site.nats_fqdn not in server.subject_alt_names:
            return CheckResult(
                "certificates", False, f"SAN does not contain {site.nats_fqdn}"
            )
        if server.status is CertificateStatus.EXPIRED:
            return CheckResult("certificates", False, "server certificate has expired")
        return CheckResult(
            "certificates", True, f"valid, {server.days_remaining} day(s) remaining"
        )

    def _check_permissions(self) -> CheckResult:
        """The private files have to stay private. Mode 600, like the shell set."""
        problems = []
        for path in (
            self._settings.private_dir / "ca-key.pem",
            self._settings.cert_dir / "server-key.pem",
            self._settings.runtime_dir / "conf" / "nats-server.conf",
            self._settings.ssh_key_path,
            # Created on first use, so an installation that has not needed it
            # yet has none - which the loop below skips rather than reports.
            self._settings.private_dir / helper_signing.KEY_FILE,
        ):
            if not path.exists():
                continue
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                problems.append(f"{path.name} is {oct(mode)}")
        return CheckResult(
            name="file_permissions",
            ok=not problems,
            detail="; ".join(problems) if problems else "private files are 0600",
        )

    def _check_overlay(self) -> CheckResult:
        """Every peer in the inventory has to be in the hub configuration.

        The configuration is a rendering of the inventory, so the two can only
        disagree if a render was missed - and the symptom of that is one probe
        that stops answering while the rest of the fleet is fine, which is the
        hardest kind of failure to attribute.
        """
        site = self._runtime.site_settings()
        overlay = OverlayRuntime(self._settings)
        if not site.overlay_enabled:
            return CheckResult(name="overlay", ok=True, detail="not enabled")

        problems = []
        if not overlay.has_hub_key():
            problems.append("the hub has no key; run 'prtg-nats overlay enable'")
        if not site.overlay_endpoint_host:
            problems.append("OVERLAY_ENDPOINT_HOST is not set")
        if site.overlay_endpoint_host == site.nats_host_ip:
            problems.append(
                "OVERLAY_ENDPOINT_HOST is NATS_HOST_IP; the tunnel would have "
                "to carry its own endpoint"
            )
        peers = overlay.peers() if overlay.has_hub_key() else ()
        if overlay.has_hub_key():
            rendered = overlay.render_hub_config()
            missing = [
                peer.nats_username
                for peer in peers
                if f"{peer.address}/32" not in rendered
            ]
            if missing:
                problems.append(f"not in the hub configuration: {', '.join(missing)}")
        if not overlay.interface_up():
            problems.append("the hub interface is not up")

        return CheckResult(
            name="overlay",
            ok=not problems,
            detail="; ".join(problems) or f"{len(peers)} peer(s)",
        )

    def _check_server_config_renders(self) -> CheckResult:
        """The strictest offline proxy for `nats-server -t`: every account
        entry validates, the template resolves."""
        try:
            NatsRuntime(self._settings).render_server_config()
        except Exception as exc:
            # An AppError says what happened in `details`; str() is only its
            # code, which would tell an operator nothing.
            detail = getattr(exc, "details", None) or str(exc)
            return CheckResult("server_config", False, str(detail)[:300])
        return CheckResult("server_config", True, "renders from the auth registry")

    # --- Live ---------------------------------------------------------------

    async def _check_health_endpoint(self) -> CheckResult:
        url = f"{self._settings.nats_monitoring_url}/healthz?js-enabled-only=true"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
            ok = response.status_code == 200
            return CheckResult("nats_health", ok, f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return CheckResult("nats_health", False, str(exc)[:200])

    async def _check_ca_endpoint(self) -> CheckResult:
        """The download endpoint must serve exactly the active CA - a stale
        copy would enroll probes against a CA the server no longer uses."""
        site = self._runtime.site_settings()
        if not site.nats_host_ip:
            return CheckResult("ca_endpoint", False, "NATS_HOST_IP is not configured")
        url = f"http://{site.nats_host_ip}:{site.ca_http_port}/nats-ca.pem"
        try:
            active = (self._settings.cert_dir / "ca.pem").read_bytes()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
            if response.status_code != 200:
                return CheckResult("ca_endpoint", False, f"HTTP {response.status_code}")
            if response.content != active:
                return CheckResult(
                    "ca_endpoint", False, "served CA differs from the active one"
                )
            return CheckResult("ca_endpoint", True, url)
        except (OSError, httpx.HTTPError) as exc:
            return CheckResult("ca_endpoint", False, str(exc)[:200])

    async def _check_authenticated_login(self) -> CheckResult:
        """Speak the NATS protocol once, as a client with real credentials."""
        site = self._runtime.site_settings()
        if not site.nats_fqdn:
            return CheckResult("nats_login", False, "NATS_FQDN is not configured")
        try:
            password = NatsRuntime(self._settings).read_password("prtg-nats")
        except Exception as exc:
            return CheckResult("nats_login", False, f"no shared credentials: {exc}")

        try:
            detail = await asyncio.wait_for(
                _nats_login(
                    host=site.nats_fqdn,
                    port=site.nats_port,
                    username="prtg-nats",
                    password=password,
                    ca_path=str(self._settings.cert_dir / "ca.pem"),
                ),
                timeout=10.0,
            )
            return CheckResult("nats_login", True, detail)
        except TimeoutError:
            return CheckResult("nats_login", False, "timed out")
        except Exception as exc:
            return CheckResult("nats_login", False, str(exc)[:200])


async def _nats_login(
    *, host: str, port: int, username: str, password: str, ca_path: str
) -> str:
    """INFO -> TLS upgrade -> CONNECT -> PING -> PONG.

    NATS starts in cleartext and upgrades - which is exactly why a plain port
    scan proves nothing and this function exists.
    """
    reader, writer = await asyncio.open_connection(host, port)
    try:
        info_line = await reader.readline()
        if not info_line.startswith(b"INFO "):
            raise ConnectionError(f"unexpected greeting: {info_line[:80]!r}")
        info = json.loads(info_line[5:])

        if info.get("tls_required", False):
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
