"""Putting a probe on the overlay, and taking it off again.

One implementation for both surfaces: the job handlers wrap these calls with
steps and a log, and ``python -m app.ops overlay`` calls them directly, so the
command line and the interface cannot drift into doing it differently.

Enabling it is here too. The settings live in the runtime, which this
container owns, and the hub is created through the Docker socket the way the
updater is - so turning the overlay on is a button rather than a shell
session on the host. It stays administrator-only for the reason the stack
update is: whoever presses it decides that a container with network-admin
rights runs on this host.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    ProbeHelperOutdatedError,
    ProbeUnreachableError,
    RuntimeStateError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.infrastructure import known_hosts
from app.infrastructure.docker import OVERLAY_IMAGE, DockerAdapter
from app.infrastructure.overlay import (
    OverlayRuntime,
    OverlaySettings,
    OverlayStatus,
    validate_endpoint_host,
    validate_mode,
    validate_public_key,
    validate_subnet,
)
from app.infrastructure.probe_helper import ProbeConnection, ProbeHelperClient
from app.infrastructure.probe_helper.protocol import OVERLAY_HELPER_VERSION
from app.infrastructure.runtime_files import ProbeInventory, RuntimeFileStore

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PeerState:
    """What the probe answered the last time it was asked."""

    nats_username: str
    mode: str
    address: str | None
    public_key: str | None
    endpoint: str | None
    interface_up: bool
    handshake_age: int | None
    route_active: bool
    direct_ok: str

    @property
    def summary(self) -> str:
        """A single word for the inventory and the interface.

        ``direct`` and ``tunnel`` are the two honest answers for a working
        probe; the rest name what is wrong, because "up" for a tunnel that has
        not handshaked in an hour would be the least useful thing to report.
        """
        if self.mode == "off":
            return "off"
        if not self.interface_up:
            return "down"
        if self.handshake_age is None:
            return "no_handshake"
        return "tunnel" if self.route_active else "direct"


class OverlayService:
    def __init__(
        self,
        settings: Settings,
        helper: ProbeHelperClient,
        docker: DockerAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._helper = helper
        self._docker = docker
        self._runtime = RuntimeFileStore(settings)
        self._overlay = OverlayRuntime(settings)

    # --- The hub ------------------------------------------------------------

    def initialise(self) -> str:
        """Hub key and rendered configuration. Safe to call again."""
        self._overlay.check_endpoint_collision()
        public_key = self._overlay.ensure_hub_key()
        self._overlay.write_hub_config()
        return public_key

    async def enable(
        self,
        *,
        endpoint_host: str,
        subnet: str | None = None,
        default_mode: str | None = None,
        port: int | None = None,
    ) -> OverlayStatus:
        """Turn the overlay on: settings, key, configuration, hub.

        Validated before anything is written. An endpoint that is the NATS
        address is the one mistake with no way back from the far side, so it
        is refused here rather than discovered when a site's tunnel drops.
        """
        current = self._overlay.settings()
        self._overlay.check_endpoint_collision(endpoint_host)
        wanted = OverlaySettings(
            enabled=True,
            endpoint_host=validate_endpoint_host(endpoint_host),
            port=port or current.port,
            subnet=validate_subnet(subnet or current.subnet),
            default_mode=validate_mode(default_mode or current.default_mode),
        )
        if self._overlay.peers() and wanted.subnet != current.subnet:
            raise ConflictError(
                params={"subnet": wanted.subnet},
                details=(
                    "probes already hold addresses from the current range; "
                    "take them off the overlay before changing it"
                ),
            )
        self._overlay.write_settings(wanted)
        self.initialise()
        await self.reconcile_hub()
        return self._overlay.status()

    async def disable(self) -> OverlayStatus:
        """Stop the hub and record that it is off.

        The peers keep their addresses and their keys. Turning the overlay off
        is not the same as retiring every probe from it, and turning it back on
        should not mean visiting each one again.
        """
        settings = self._overlay.settings()
        self._overlay.write_settings(
            OverlaySettings(
                enabled=False,
                endpoint_host=settings.endpoint_host,
                port=settings.port,
                subnet=settings.subnet,
                default_mode=settings.default_mode,
            )
        )
        await self.reconcile_hub()
        return self._overlay.status()

    async def reconcile_hub(self) -> None:
        """Make the running hub agree with the settings.

        Called on every enable and disable, and once when the API starts - a
        host that rebooted, or a stack update that took the container with it,
        should not need anybody to notice.
        """
        if self._docker is None or not self._docker.available:
            return
        wanted = self._overlay.settings().enabled
        running = await self._docker.overlay_hub_running()
        if wanted == running:
            return
        if not wanted:
            await self._docker.remove_overlay_hub()
            logger.info("overlay hub stopped")
            return
        if not await self._docker.image_exists(OVERLAY_IMAGE):
            raise RuntimeStateError(
                params={"image": OVERLAY_IMAGE},
                details=(
                    "the overlay image has not been built; update the stack "
                    "once so compose builds it"
                ),
            )
        # Remove first: a container that exists but is not running carries the
        # configuration it was created with, and the runtime path may have
        # moved since.
        await self._docker.remove_overlay_hub()
        container_id = await self._docker.create_overlay_hub()
        await self._docker.start_container(container_id)
        logger.info("overlay hub started", extra={"container": container_id})

    def status(self) -> OverlayStatus:
        return self._overlay.status()

    # --- One probe ----------------------------------------------------------

    async def attach(self, username: str, mode: str | None = None) -> PeerState:
        """Give a probe an address on the overlay and configure its tunnel.

        The address is allocated first and the probe's public key arrives with
        the answer, which is the only order that works: the probe generates
        its own key, so the platform cannot know it before it has asked.
        """
        site = self._runtime.site_settings()
        settings = self._overlay.settings()
        mode = validate_mode(mode or settings.default_mode)
        if not settings.enabled:
            raise RuntimeStateError(
                details="the overlay is not enabled for this installation"
            )
        endpoint = settings.endpoint
        if not endpoint:
            raise RuntimeStateError(details="OVERLAY_ENDPOINT_HOST is not configured")
        if not site.nats_host_ip:
            raise RuntimeStateError(details="NATS_HOST_IP is not configured")
        self._overlay.check_endpoint_collision()

        inventory = self._runtime.read_probe(username)
        await self._require_overlay_helper(inventory)
        address = inventory.overlay_address or self._overlay.allocate_address()

        response = await self._helper.overlay_configure(
            ProbeConnection.for_probe(inventory),
            mode=mode,
            hub_public_key=self.initialise(),
            endpoint=endpoint,
            address=address,
            subnet=settings.subnet,
            nats_host_ip=site.nats_host_ip,
            nats_port=site.nats_port,
        )
        public_key = validate_public_key(response.required("overlay_public_key"))

        self._runtime.write_probe_overlay(
            username,
            address=address,
            public_key=public_key,
            mode=mode,
        )
        # Only now: a peer in the hub configuration whose probe never answered
        # would be an address handed out to nothing.
        self._overlay.write_hub_config()
        self._pin_overlay_host_key(inventory, address)
        await self._allow_hub_as_source(username, address)
        return await self.refresh(username)

    async def set_mode(
        self, username: str, mode: str, *, force: bool = False
    ) -> PeerState:
        """Change when this probe's NATS traffic takes the tunnel.

        Switching to ``off`` deliberately goes over the ordinary address: the
        request takes the tunnel down, and sending it through that same tunnel
        would cut the session carrying it. A probe that does not answer there
        is refused rather than stranded.
        """
        mode = validate_mode(mode)
        inventory = self._runtime.read_probe(username)
        if not inventory.on_overlay:
            raise ConflictError(
                params={"probe": username},
                details="this probe is not on the overlay",
            )
        site = self._runtime.site_settings()
        settings = self._overlay.settings()
        endpoint = settings.endpoint
        if not endpoint or not site.nats_host_ip:
            raise RuntimeStateError(details="the overlay is not fully configured")

        connection = ProbeConnection.for_probe(inventory)
        if mode == "off" and not force:
            connection = await self._direct_connection(inventory)

        await self._helper.overlay_configure(
            connection,
            mode=mode,
            hub_public_key=self._overlay.hub_public_key(),
            endpoint=endpoint,
            address=inventory.overlay_address or "",
            subnet=settings.subnet,
            nats_host_ip=site.nats_host_ip,
            nats_port=site.nats_port,
        )
        self._runtime.write_probe_overlay(
            username,
            address=inventory.overlay_address or "",
            public_key=inventory.overlay_public_key or "",
            mode=mode,
        )
        self._overlay.write_hub_config()
        return await self.refresh(username)

    async def detach(self, username: str, *, force: bool = False) -> None:
        """Take the probe off the overlay and give its address back."""
        inventory = self._runtime.read_probe(username)
        if not inventory.on_overlay:
            return

        connection = ProbeConnection.for_probe(inventory)
        if not force:
            connection = await self._direct_connection(inventory)
        # The source list goes back first, while the tunnel is still there to
        # carry the request if the ordinary route is the one that is broken.
        await self._restore_source(username, connection)
        await self._helper.overlay_remove(connection)

        self._runtime.clear_probe_overlay(username)
        self._overlay.write_hub_config()
        if inventory.overlay_address:
            known_hosts.forget(
                self._settings.ssh_known_hosts_path,
                inventory.overlay_address,
                inventory.ssh_port,
            )

    async def refresh(self, username: str) -> PeerState:
        """Ask the probe what the overlay is doing and record the answer."""
        inventory = self._runtime.read_probe(username)
        response = await self._helper.overlay_info(ProbeConnection.for_probe(inventory))
        state = _peer_state(username, response.values)
        if inventory.on_overlay:
            self._runtime.write_probe_overlay(
                username,
                address=inventory.overlay_address or "",
                public_key=inventory.overlay_public_key or "",
                mode=inventory.overlay_mode,
                last_state=state.summary,
            )
        return state

    # --- Pieces -------------------------------------------------------------

    async def _require_overlay_helper(self, inventory: ProbeInventory) -> None:
        info = await self._helper.probe_info(ProbeConnection.for_probe(inventory))
        raw = info.value("helper_version")
        version = int(raw) if raw and raw.isdigit() else 0
        if version < OVERLAY_HELPER_VERSION:
            raise ProbeHelperOutdatedError(
                params={
                    "probe": inventory.nats_username,
                    "version": str(version),
                    "required": str(OVERLAY_HELPER_VERSION),
                },
                details="the probe helper is too old to know about the overlay",
            )

    async def _direct_connection(self, inventory: ProbeInventory) -> ProbeConnection:
        """The probe's ordinary address, checked before it is relied on."""
        if not inventory.ssh_host:
            raise ConflictError(
                params={"probe": inventory.nats_username},
                details="the probe has no address besides the overlay",
            )
        connection = ProbeConnection(
            nats_username=inventory.nats_username,
            host=inventory.ssh_host,
            port=inventory.ssh_port,
        )
        try:
            await self._helper.probe_info(connection)
        except ProbeUnreachableError as error:
            raise ConflictError(
                params={
                    "probe": inventory.nats_username,
                    "host": inventory.ssh_host,
                },
                details=(
                    "the probe does not answer on its ordinary address, so "
                    "taking the tunnel down would leave it unreachable"
                ),
            ) from error
        return connection

    def _pin_overlay_host_key(self, inventory: ProbeInventory, address: str) -> None:
        """Pin the keys already trusted for this probe under its new address.

        Copied, never re-scanned: asking the host again for the keys it should
        prove it has would turn pinning into trust on first use.
        """
        keys = known_hosts.read_pinned(
            self._settings.ssh_known_hosts_path,
            inventory.ssh_host,
            inventory.ssh_port,
        )
        if not keys:
            logger.warning(
                "no pinned host key to carry over to the overlay address",
                extra={"probe": inventory.nats_username},
            )
            return
        known_hosts.pin(
            self._settings.ssh_known_hosts_path, address, inventory.ssh_port, keys
        )

    async def _allow_hub_as_source(self, username: str, address: str) -> None:
        """Add the hub to the from= list on the probe's management key.

        Added, never substituted: the probe has to stay reachable from the
        address it is reachable from today, or a broken tunnel would be a lost
        probe rather than a slower one.
        """
        site = self._runtime.site_settings()
        sources = [
            source.strip()
            for source in (site.ssh_source_cidr or "").split(",")
            if source.strip()
        ]
        hub = f"{self._overlay.settings().hub_address}/32"
        if hub not in sources:
            sources.append(hub)
        inventory = self._runtime.read_probe(username)
        await self._helper.access_source(
            ProbeConnection.for_probe(inventory), ",".join(sources)
        )

    async def _restore_source(self, username: str, connection: ProbeConnection) -> None:
        site = self._runtime.site_settings()
        sources = [
            source.strip()
            for source in (site.ssh_source_cidr or "").split(",")
            if source.strip()
        ]
        if not sources:
            raise ValidationFailedError(
                params={"probe": username},
                details="MPP_SSH_SOURCE_CIDR is empty; refusing to rewrite the key",
            )
        await self._helper.access_source(connection, ",".join(sources))


def _peer_state(username: str, values: dict[str, str]) -> PeerState:
    age = values.get("overlay_handshake_age", "none")
    return PeerState(
        nats_username=username,
        mode=values.get("overlay_mode") or "off",
        address=values.get("overlay_address") or None,
        public_key=(values.get("overlay_public_key") or "").replace("none", "") or None,
        endpoint=values.get("overlay_endpoint") or None,
        interface_up=values.get("overlay_interface_up") == "true",
        handshake_age=int(age) if age.isdigit() else None,
        route_active=values.get("overlay_route_active") == "true",
        direct_ok=values.get("overlay_direct_ok") or "unknown",
    )
