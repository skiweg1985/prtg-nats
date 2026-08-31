"""The WireGuard overlay: hub key, address allocation, peer rendering.

The same relationship ``auth-users/*.auth`` has to ``nats-server.conf``: the
probe inventory is the source of truth about who is a peer, and
``runtime/overlay/prtgnats0.conf`` is a rendering of it. Nothing here holds
state of its own, which is what lets the shell tooling and the interface both
put a probe on the overlay without a second register to keep in step
(ADR 0002).

The hub key lives in ``runtime/overlay/`` rather than ``runtime/private/``
because the hub runs in a container that must not be able to read the CA key -
the same reason the interface certificate is not in ``certs/``.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    RuntimeStateError,
    ValidationFailedError,
)
from app.infrastructure.runtime_files import RuntimeFileStore, overlay_address_at

INTERFACE = "prtgnats0"
# A WireGuard key is 32 raw bytes; base64 makes that 44 characters with one
# padding character. Anything else is not a key, and a peer block built from
# it would take the whole hub configuration down rather than just that peer.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")
MODES = ("off", "auto", "on")
# The hub is the first host address, probes start after the first 256. That
# leaves the low addresses for anything the installation itself might need
# later, and it makes a peer address readable at a glance: 10.83.1.7 is the
# seventh probe, not an offset somebody has to compute.
FIRST_PEER_INDEX = 256


def generate_keypair() -> tuple[str, str]:
    """A private and a public key, in the base64 WireGuard speaks.

    Generated here rather than by shelling out to ``wg genkey``: the API
    container has no wireguard-tools, and X25519 is X25519. The probe's own
    key is a different matter - that one is generated on the probe, so the
    private half never travels.
    """
    private = X25519PrivateKey.generate()
    private_bytes = private.private_bytes_raw()
    public_bytes = private.public_key().public_bytes_raw()
    return (
        b64encode(private_bytes).decode("ascii"),
        b64encode(public_bytes).decode("ascii"),
    )


def public_key_for(private_key: str) -> str:
    raw = b64decode(private_key.encode("ascii"))
    return b64encode(
        X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw()
    ).decode("ascii")


def validate_public_key(value: str) -> str:
    if not KEY_PATTERN.match(value):
        raise ValidationFailedError(
            params={"key": value[:8]}, details="not a WireGuard public key"
        )
    return value


def validate_mode(value: str) -> str:
    if value not in MODES:
        raise ValidationFailedError(
            params={"mode": value, "allowed": ", ".join(MODES)},
            details="unknown overlay mode",
        )
    return value


@dataclass(frozen=True, slots=True)
class OverlayPeer:
    nats_username: str
    address: str
    public_key: str
    mode: str


@dataclass(frozen=True, slots=True)
class OverlayStatus:
    enabled: bool
    endpoint: str | None
    subnet: str
    hub_address: str
    hub_public_key: str | None
    default_mode: str
    peers: tuple[OverlayPeer, ...]
    interface_up: bool


class OverlayRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime = RuntimeFileStore(settings)

    # --- Paths --------------------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._settings.runtime_dir / "overlay"

    @property
    def hub_key_path(self) -> Path:
        return self.directory / "hub-key"

    @property
    def hub_public_path(self) -> Path:
        return self.directory / "hub.pub"

    @property
    def config_path(self) -> Path:
        return self.directory / f"{INTERFACE}.conf"

    # --- Hub key ------------------------------------------------------------

    def ensure_hub_key(self) -> str:
        """Create the hub key pair once and return the public half.

        Idempotent on purpose: every path that needs the public key calls it,
        and a second key would silently orphan every peer already configured
        against the first.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        if self.hub_key_path.is_file():
            return self.hub_public_key()
        private_key, public_key = generate_keypair()
        self.hub_key_path.touch(mode=0o600, exist_ok=True)
        self.hub_key_path.chmod(0o600)
        self.hub_key_path.write_text(f"{private_key}\n", encoding="utf-8")
        self.hub_public_path.touch(mode=0o644, exist_ok=True)
        self.hub_public_path.chmod(0o644)
        self.hub_public_path.write_text(f"{public_key}\n", encoding="utf-8")
        return public_key

    def hub_public_key(self) -> str:
        if not self.hub_key_path.is_file():
            raise RuntimeStateError(details="the overlay hub key does not exist yet")
        return public_key_for(self.hub_key_path.read_text(encoding="utf-8").strip())

    def has_hub_key(self) -> bool:
        return self.hub_key_path.is_file()

    # --- Peers --------------------------------------------------------------

    def peers(self) -> tuple[OverlayPeer, ...]:
        found = []
        for probe in self._runtime.read_all_probes():
            address = probe.overlay_address
            public_key = probe.overlay_public_key
            if not address or not public_key:
                continue
            found.append(
                OverlayPeer(
                    nats_username=probe.nats_username,
                    address=address,
                    public_key=public_key,
                    mode=probe.overlay_mode,
                )
            )
        return tuple(sorted(found, key=lambda peer: _address_key(peer.address)))

    def allocate_address(self) -> str:
        """The lowest free peer address in the subnet.

        Reuses a gap left by a retired probe rather than always growing: an
        installation that adds and removes probes for years should not run out
        of a /16 because of it. Callers hold the overlay resource lock, so two
        enrollments cannot arrive at the same answer.
        """
        site = self._runtime.site_settings()
        taken = {peer.address for peer in self.peers()}
        network = ipaddress.IPv4Network(site.overlay_subnet, strict=False)
        for index in range(FIRST_PEER_INDEX, network.num_addresses - 1):
            candidate = overlay_address_at(site.overlay_subnet, index)
            if candidate not in taken:
                return candidate
        raise ConflictError(
            params={"subnet": site.overlay_subnet},
            details="no free address left in the overlay subnet",
        )

    # --- Rendering ----------------------------------------------------------

    def render_hub_config(self) -> str:
        """The hub interface and one peer block per probe on the overlay.

        A peer in mode ``off`` still appears here. Its tunnel is down on the
        probe's side, and re-adding the peer when it comes back would mean the
        hub forgetting an address it has already handed out.
        """
        site = self._runtime.site_settings()
        private_key = self.hub_key_path.read_text(encoding="utf-8").strip()
        prefix = ipaddress.IPv4Network(site.overlay_subnet, strict=False).prefixlen
        lines = [
            "# Generated by prtg-nats from the probe inventory. Do not edit:",
            "# every change here is lost the next time a probe is added.",
            "[Interface]",
            f"Address = {site.overlay_hub_address}/{prefix}",
            f"ListenPort = {site.overlay_port}",
            f"PrivateKey = {private_key}",
        ]
        for peer in self.peers():
            lines.extend(
                [
                    "",
                    f"# {peer.nats_username} ({peer.mode})",
                    "[Peer]",
                    f"PublicKey = {validate_public_key(peer.public_key)}",
                    # A single address, not the subnet: this is also the
                    # inbound filter, and a peer allowed to send from any
                    # overlay address could answer for another probe.
                    f"AllowedIPs = {peer.address}/32",
                ]
            )
        return "\n".join(lines) + "\n"

    def write_hub_config(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        config = self.render_hub_config()
        self.config_path.touch(mode=0o600, exist_ok=True)
        self.config_path.chmod(0o600)
        self.config_path.write_text(config, encoding="utf-8")

    # --- Status -------------------------------------------------------------

    def interface_up(self) -> bool:
        """Whether the hub interface exists in this network namespace.

        The API container shares the host namespace with the hub, so this is
        the real interface and not a guess from the configuration file.
        """
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["/sbin/ip", "link", "show", INTERFACE],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def status(self) -> OverlayStatus:
        site = self._runtime.site_settings()
        return OverlayStatus(
            enabled=site.overlay_enabled,
            endpoint=site.overlay_endpoint,
            subnet=site.overlay_subnet,
            hub_address=site.overlay_hub_address,
            hub_public_key=self.hub_public_key() if self.has_hub_key() else None,
            default_mode=site.overlay_default_mode,
            peers=self.peers(),
            interface_up=self.interface_up(),
        )

    # --- Guards -------------------------------------------------------------

    def check_endpoint_collision(self) -> None:
        """Refuse an endpoint that is the NATS address itself.

        Routing NATS_HOST_IP through the tunnel would then route the tunnel's
        own endpoint into the tunnel, and the probe would lose both paths at
        once. Better to say so while somebody is configuring than to find out
        when a site-to-site tunnel drops.
        """
        site = self._runtime.site_settings()
        if not site.overlay_endpoint_host or not site.nats_host_ip:
            return
        if site.overlay_endpoint_host == site.nats_host_ip:
            raise ValidationFailedError(
                params={
                    "endpoint": site.overlay_endpoint_host,
                    "nats_host_ip": site.nats_host_ip,
                },
                details=(
                    "OVERLAY_ENDPOINT_HOST is NATS_HOST_IP; the tunnel would "
                    "have to carry its own endpoint"
                ),
            )


def _address_key(address: str) -> int:
    return int(ipaddress.IPv4Address(address))
