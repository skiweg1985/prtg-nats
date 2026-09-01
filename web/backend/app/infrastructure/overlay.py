"""The WireGuard overlay: hub key, address allocation, peer rendering.

The same relationship ``auth-users/*.auth`` has to ``nats-server.conf``: the
probe inventory is the source of truth about who is a peer, and
``runtime/overlay/prtgnats0.conf`` is a rendering of it. That is what lets the
shell tooling and the interface both put a probe on the overlay without a
second register to keep in step (ADR 0002).

``runtime/overlay/pending/`` is the one exception, and it earns it. A probe
enrolling over the tunnel has no inventory entry yet - it cannot reach the
platform to create one - so its peer has to exist before it speaks. Those
files hold the private key of a peer that is not a probe yet, they expire with
the invitation that created them, and every one of them is gone the moment the
probe reports in or the invitation is revoked (ADR 0010).

The hub key lives in ``runtime/overlay/`` rather than ``runtime/private/``
because the hub runs in a container that must not be able to read the CA key -
the same reason the interface certificate is not in ``certs/``.

Its settings live there too, and not in ``.env``. That file sits beside the
checkout on the host, which the API container does not have - so anything kept
there is something an administrator has to reach a shell for. The runtime is
the source of truth for everything else about this installation (ADR 0002),
and the API owns it, so the overlay's own switch belongs there as well.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from base64 import b64decode, b64encode
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    RuntimeStateError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.infrastructure.runtime_files import (
    RuntimeFileStore,
    overlay_address_at,
    read_env_file,
)

logger = get_logger(__name__)

INTERFACE = "prtgnats0"
# A WireGuard key is 32 raw bytes; base64 makes that 44 characters with one
# padding character. Anything else is not a key, and a peer block built from
# it would take the whole hub configuration down rather than just that peer.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")
# An invitation id names a file under pending/, so the shape it is allowed to
# have is spelled out rather than assumed from where it happens to come from.
TOKEN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MODES = ("off", "auto", "on")
# The hub is the first host address, probes start after the first 256. That
# leaves the low addresses for anything the installation itself might need
# later, and it makes a peer address readable at a glance: 10.83.1.7 is the
# seventh probe, not an offset somebody has to compute.
FIRST_PEER_INDEX = 256


def generate_keypair() -> tuple[str, str]:
    """A private and a public key, in the base64 WireGuard speaks.

    Generated here rather than by shelling out to ``wg genkey``: the API
    container has no wireguard-tools, and X25519 is X25519.

    A probe normally generates its own key, so the private half never travels.
    The exception is a probe enrolling over the tunnel: it cannot reach the
    platform to report a key it generated, so this makes the pair for it and
    the bootstrap script carries the private half once (ADR 0010).
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


# Hostname or IPv4 address, no scheme and no port - the port is its own
# setting. The same shape mpp_validate_nats_host accepts in the shell, so an
# endpoint that works here works there.
ENDPOINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")


def validate_endpoint_host(value: str) -> str:
    if not ENDPOINT_PATTERN.match(value):
        raise ValidationFailedError(
            params={"endpoint": value},
            details="not a usable endpoint address",
        )
    return value


def validate_subnet(value: str) -> str:
    """Narrower than /30 leaves no room for a hub and one peer; wider than /8
    is a range nobody meant to hand to one installation."""
    try:
        network = ipaddress.IPv4Network(value, strict=False)
    except ValueError as error:
        raise ValidationFailedError(
            params={"subnet": value}, details=f"invalid overlay subnet: {error}"
        ) from error
    if not 8 <= network.prefixlen <= 30:
        raise ValidationFailedError(
            params={"subnet": value},
            details="an overlay subnet has to be between /8 and /30",
        )
    return str(network)


def validate_mode(value: str) -> str:
    if value not in MODES:
        raise ValidationFailedError(
            params={"mode": value, "allowed": ", ".join(MODES)},
            details="unknown overlay mode",
        )
    return value


DEFAULT_SUBNET = "10.83.0.0/16"
DEFAULT_PORT = 51820
DEFAULT_MODE = "auto"


@dataclass(frozen=True, slots=True)
class OverlaySettings:
    """What this installation's overlay is, as the runtime records it."""

    enabled: bool = False
    # Where a probe dials the hub. Its own setting rather than NATS_FQDN,
    # because it has to be reachable exactly when NATS_FQDN is not: on a site
    # whose NATS address is internal, this is the public one.
    endpoint_host: str | None = None
    port: int = DEFAULT_PORT
    subnet: str = DEFAULT_SUBNET
    default_mode: str = DEFAULT_MODE

    @property
    def hub_address(self) -> str:
        """The first host address of the subnet. Derived, never configured -
        a hub address that could disagree with its subnet would be one
        setting too many."""
        return overlay_address_at(self.subnet, 1)

    @property
    def endpoint(self) -> str | None:
        if not self.endpoint_host:
            return None
        return f"{self.endpoint_host}:{self.port}"


@dataclass(frozen=True, slots=True)
class OverlayPeer:
    nats_username: str
    address: str
    public_key: str
    mode: str
    # A peer the platform created for an invitation that has not been redeemed.
    # It belongs in the hub configuration - that is the whole point, the probe
    # cannot report in without it - but not in a status list that claims to
    # show probes.
    pending: bool = False


@dataclass(frozen=True, slots=True)
class PendingPeer:
    """A peer the platform created before the probe could ask for one.

    It carries the private key, which no other peer on this platform does.
    That is the trade ADR 0010 makes: a probe that cannot reach the platform
    cannot generate a key and report it, so the platform generates one and
    hands it over in the bootstrap script.
    """

    token_id: str
    nats_username: str
    address: str
    private_key: str
    public_key: str
    mode: str


def _pending_from(token_id: str, values: Mapping[str, str]) -> PendingPeer | None:
    """One reservation, or None where the file says nothing usable.

    A half-written or hand-edited file is skipped rather than raised on: it
    would otherwise take down every rendering of the hub configuration, which
    is a far worse failure than one peer that has to be reissued.
    """
    address = values.get("ADDRESS") or ""
    private_key = values.get("PRIVATE_KEY") or ""
    public_key = values.get("PUBLIC_KEY") or ""
    username = values.get("NATS_USERNAME") or ""
    if not address or not username or not KEY_PATTERN.match(public_key):
        logger.warning("overlay reservation unusable", extra={"token": token_id[:8]})
        return None
    return PendingPeer(
        token_id=token_id,
        nats_username=username,
        address=address,
        private_key=private_key,
        public_key=public_key,
        mode=values.get("MODE") or DEFAULT_MODE,
    )


@dataclass(frozen=True, slots=True)
class OverlayStatus:
    settings: OverlaySettings
    hub_public_key: str | None
    peers: tuple[OverlayPeer, ...]
    # None where the container could not tell - not the same as "down".
    interface_up: bool | None

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def endpoint(self) -> str | None:
        return self.settings.endpoint

    @property
    def subnet(self) -> str:
        return self.settings.subnet

    @property
    def hub_address(self) -> str:
        return self.settings.hub_address

    @property
    def default_mode(self) -> str:
        return self.settings.default_mode


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

    @property
    def settings_path(self) -> Path:
        return self.directory / "settings"

    @property
    def pending_dir(self) -> Path:
        return self.directory / "pending"

    # --- Settings -----------------------------------------------------------

    def settings(self) -> OverlaySettings:
        """What the runtime says the overlay is. Absent file means off."""
        values = read_env_file(self.settings_path)
        return OverlaySettings(
            enabled=values.get("ENABLED") == "true",
            endpoint_host=values.get("ENDPOINT_HOST") or None,
            port=_port(values.get("PORT")),
            subnet=values.get("SUBNET") or DEFAULT_SUBNET,
            default_mode=values.get("DEFAULT_MODE") or DEFAULT_MODE,
        )

    def write_settings(self, settings: OverlaySettings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        content = (
            "# Written by prtg-nats. Change it through the interface or\n"
            "# ./prtg-nats overlay enable.\n"
            f"ENABLED={'true' if settings.enabled else 'false'}\n"
            f"ENDPOINT_HOST={settings.endpoint_host or ''}\n"
            f"PORT={settings.port}\n"
            f"SUBNET={settings.subnet}\n"
            f"DEFAULT_MODE={settings.default_mode}\n"
        )
        self.settings_path.touch(mode=0o600, exist_ok=True)
        self.settings_path.chmod(0o600)
        self.settings_path.write_text(content, encoding="utf-8")

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

    # --- Peers reserved for an invitation -----------------------------------

    def _pending_path(self, token_id: str) -> Path:
        # The id names a file, so it is checked rather than trusted. It comes
        # from the database today; the check is what keeps that from being a
        # load-bearing assumption.
        if not TOKEN_ID_PATTERN.match(token_id):
            raise ValidationFailedError(
                params={"token": token_id[:8]},
                details="not an invitation id",
            )
        return self.pending_dir / token_id

    def write_pending_peer(self, peer: PendingPeer) -> None:
        """Reserve a peer for an invitation that has not been redeemed.

        The private key is written here rather than kept in the invitation
        because the bootstrap script may be fetched more than once - a
        half-finished run has to be retryable - and every fetch has to render
        the same key. It is also the reason for 0700 and 0600 below.
        """
        path = self._pending_path(peer.token_id)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.chmod(0o700)
        content = (
            "# Written by prtg-nats for an invitation. Removed when the probe\n"
            "# reports in, or when the invitation is revoked or expires.\n"
            f"NATS_USERNAME={peer.nats_username}\n"
            f"ADDRESS={peer.address}\n"
            f"PRIVATE_KEY={peer.private_key}\n"
            f"PUBLIC_KEY={peer.public_key}\n"
            f"MODE={peer.mode}\n"
        )
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(content, encoding="utf-8")

    def read_pending_peer(self, token_id: str) -> PendingPeer | None:
        path = self._pending_path(token_id)
        if not path.is_file():
            return None
        return _pending_from(token_id, read_env_file(path))

    def pending_peers(self) -> tuple[PendingPeer, ...]:
        if not self.pending_dir.is_dir():
            return ()
        found = []
        for path in sorted(self.pending_dir.iterdir()):
            if not path.is_file() or not TOKEN_ID_PATTERN.match(path.name):
                continue
            peer = _pending_from(path.name, read_env_file(path))
            if peer is not None:
                found.append(peer)
        return tuple(found)

    def drop_pending_peer(self, token_id: str) -> bool:
        """Forget a reservation. True when there was one to forget."""
        path = self._pending_path(token_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def prune_pending_peers(self, keep: Iterable[str]) -> tuple[str, ...]:
        """Drop every reservation whose invitation is no longer open.

        Not housekeeping. A reservation is a working overlay key, and an
        invitation expires while the key it handed out would not - so the key
        has to go when the invitation does. Called wherever the set of open
        invitations changes, and once at startup for the ones that expired
        while nothing was running.
        """
        keeping = set(keep)
        dropped = []
        for peer in self.pending_peers():
            if peer.token_id in keeping:
                continue
            self.drop_pending_peer(peer.token_id)
            dropped.append(peer.token_id)
        if dropped:
            logger.info("overlay reservations dropped", extra={"count": len(dropped)})
        return tuple(dropped)

    # --- Peers --------------------------------------------------------------

    def peers(self) -> tuple[OverlayPeer, ...]:
        found = [
            OverlayPeer(
                nats_username=peer.nats_username,
                address=peer.address,
                public_key=peer.public_key,
                mode=peer.mode,
                pending=True,
            )
            for peer in self.pending_peers()
        ]
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
        # An enrolled probe outranks a reservation on the same address. The two
        # overlap for the moment between the callback writing the inventory and
        # the reservation being dropped, and two peer blocks for one address
        # would be a hub configuration wg refuses as a whole.
        enrolled = {peer.address for peer in found if not peer.pending}
        found = [
            peer for peer in found if not peer.pending or peer.address not in enrolled
        ]
        return tuple(sorted(found, key=lambda peer: _address_key(peer.address)))

    def allocate_address(self, reserved: Iterable[str] = ()) -> str:
        """The lowest free peer address in the subnet.

        Reuses a gap left by a retired probe rather than always growing: an
        installation that adds and removes probes for years should not run out
        of a /16 because of it.

        ``reserved`` is for addresses that are spoken for but not yet a peer -
        an invitation that has been issued and not redeemed. Without it two
        invitations open at once would be promised the same address, and the
        second probe to report in would take the first one's tunnel down.
        """
        settings = self.settings()
        taken = {peer.address for peer in self.peers()} | set(reserved)
        network = ipaddress.IPv4Network(settings.subnet, strict=False)
        for index in range(FIRST_PEER_INDEX, network.num_addresses - 1):
            candidate = overlay_address_at(settings.subnet, index)
            if candidate not in taken:
                return candidate
        raise ConflictError(
            params={"subnet": settings.subnet},
            details="no free address left in the overlay subnet",
        )

    # --- Rendering ----------------------------------------------------------

    def render_hub_config(self) -> str:
        """The hub interface and one peer block per probe on the overlay.

        A peer in mode ``off`` still appears here. Its tunnel is down on the
        probe's side, and re-adding the peer when it comes back would mean the
        hub forgetting an address it has already handed out.

        A peer reserved for an invitation appears too, marked as such. It is
        the only way a probe that cannot reach the platform can be let in: the
        hub has to know it before it speaks, not after.
        """
        settings = self.settings()
        private_key = self.hub_key_path.read_text(encoding="utf-8").strip()
        prefix = ipaddress.IPv4Network(settings.subnet, strict=False).prefixlen
        lines = [
            "# Generated by prtg-nats from the probe inventory. Do not edit:",
            "# every change here is lost the next time a probe is added.",
            "[Interface]",
            f"Address = {settings.hub_address}/{prefix}",
            f"ListenPort = {settings.port}",
            f"PrivateKey = {private_key}",
        ]
        for peer in self.peers():
            note = "invitation, not yet redeemed" if peer.pending else peer.mode
            lines.extend(
                [
                    "",
                    f"# {peer.nats_username} ({note})",
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

    def interface_up(self) -> bool | None:
        """Whether the hub interface exists in this network namespace, or None
        when this container cannot tell.

        The API container shares the host namespace with the hub, so a reading
        here is the real interface and not a guess from the configuration file.

        Three states rather than two, and that is the point. This used to name
        "/sbin/ip" outright, the image did not carry iproute2, and the
        FileNotFoundError landed in the same branch as a downed interface - so
        a healthy hub was reported as down on the page, in verify and on the
        command line, with nothing anywhere to say why. A tool that is not
        there is not an answer about the interface, and saying so beats
        guessing in either direction.
        """
        executable = shutil.which("ip")
        if executable is None:
            logger.warning("cannot read the overlay interface: iproute2 is missing")
            return None
        try:
            result = subprocess.run(  # noqa: S603 - resolved argv, no shell
                [executable, "link", "show", INTERFACE],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.returncode == 0

    def status(self) -> OverlayStatus:
        # Reservations are left out: this list is read as "the probes on the
        # overlay", and an address promised to an invitation is not a probe.
        # The invitation itself is where an operator sees it, with an expiry
        # beside it.
        return OverlayStatus(
            settings=self.settings(),
            hub_public_key=self.hub_public_key() if self.has_hub_key() else None,
            peers=tuple(peer for peer in self.peers() if not peer.pending),
            interface_up=self.interface_up(),
        )

    # --- Guards -------------------------------------------------------------

    def check_endpoint_collision(self, endpoint_host: str | None = None) -> None:
        """Refuse an endpoint that is the NATS address itself.

        Routing NATS_HOST_IP through the tunnel would then route the tunnel's
        own endpoint into the tunnel, and the probe would lose both paths at
        once. Better to say so while somebody is configuring than to find out
        when a site-to-site tunnel drops.
        """
        site = self._runtime.site_settings()
        endpoint_host = endpoint_host or self.settings().endpoint_host
        if not endpoint_host or not site.nats_host_ip:
            return
        if endpoint_host == site.nats_host_ip:
            raise ValidationFailedError(
                params={
                    "endpoint": endpoint_host,
                    "nats_host_ip": site.nats_host_ip,
                },
                details=(
                    "OVERLAY_ENDPOINT_HOST is NATS_HOST_IP; the tunnel would "
                    "have to carry its own endpoint"
                ),
            )


def _address_key(address: str) -> int:
    return int(ipaddress.IPv4Address(address))


def _port(value: str | None) -> int:
    """A port, or the default. A settings file somebody edited by hand is not
    a reason for the hub to refuse to start."""
    if value and value.isdigit() and 1 <= int(value) <= 65535:
        return int(value)
    return DEFAULT_PORT
