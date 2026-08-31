"""Initialise and maintain the server-side runtime, natively.

This is what remains of init-runtime.sh, renew-server-certificate.sh,
manage-users.sh, rotate-password.sh and backup-jetstream.sh once each is a
Python function with the same file formats and the same refusals.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    NatsReloadRefusedError,
    NotFoundError,
    RuntimeStateError,
)
from app.infrastructure.docker import JETSTREAM_VOLUME, DockerAdapter, StackContainer
from app.infrastructure.nats import NatsMonitoringClient
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.pki import Pki
from app.infrastructure.runtime_files import RuntimeFileStore, SiteSettings

# How long a configuration reload has to show up in the monitoring endpoint
# before it counts as refused. NATS applies it synchronously, but the signal
# goes through the Docker daemon and the endpoint is polled - two seconds were
# tight enough that a busy host produced a refusal for a reload that worked.
RELOAD_VERIFY_INTERVAL = 0.25
RELOAD_VERIFY_ATTEMPTS = 20


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BackupFile:
    name: str
    kind: str  # "runtime" | "jetstream"
    size_bytes: int
    created_at: datetime
    sha256: str | None


class ProvisioningService:
    def __init__(self, settings: Settings, docker: DockerAdapter) -> None:
        self._settings = settings
        self._docker = docker
        self._pki = Pki(settings)
        self._nats = NatsRuntime(settings)
        self._runtime = RuntimeFileStore(settings)

    # --- First-run setup ----------------------------------------------------

    def initialise_runtime(self) -> None:
        """Create everything setup used to create, refusing to overwrite.

        Same guard list as the retired script: any existing protected file
        stops the run - rotation is for changing state, initialisation is for
        empty directories.
        """
        site = self._runtime.site_settings()
        if not site.is_configured or not site.nats_fqdn:
            raise RuntimeStateError(
                details="site settings are incomplete; write .env first "
                "(NATS_FQDN, NATS_HOST_IP, PRTG_CORE_IP)"
            )

        protected = [
            self._settings.private_dir / "ca-key.pem",
            self._settings.cert_dir / "ca.pem",
            self._settings.cert_dir / "server-key.pem",
            self._settings.cert_dir / "server.pem",
            self._settings.credential_dir / "prtg-nats.env",
            self._settings.auth_user_dir / "prtg-nats.auth",
            self._settings.runtime_dir / "conf" / "nats-server.conf",
        ]
        existing = [str(path) for path in protected if path.exists()]
        if existing:
            raise ConflictError(
                params={"paths": existing},
                details="refusing to overwrite existing runtime state",
            )

        self._create_directories()
        self._pki.create_ca(organization=site.ca_organization)
        self._pki.issue_server_certificate(
            fqdn=site.nats_fqdn,
            host_ip=site.nats_host_ip,
            archive=False,
            overlay_address=_overlay_san(site),
        )
        # The reverse proxy serves the interface with a certificate from this
        # same CA, so trusting the CA once covers the browser, the NATS server
        # and the enrolment channel.
        self._pki.issue_web_certificate(
            fqdn=site.web_fqdn, host_ip=site.nats_host_ip, archive=False
        )
        self._pki.ensure_management_key(fqdn=site.nats_fqdn)
        # Creates credentials, the auth entry and the server configuration in
        # one step; the password stays in the root-only credential file.
        self._nats.create_account("prtg-nats")
        self.publish_public_material()

    def _create_directories(self) -> None:
        for path in (
            self._settings.cert_dir,
            self._settings.private_dir,
            self._settings.private_dir / "ssh",
            self._settings.credential_dir,
            self._settings.auth_user_dir,
            self._settings.probe_dir,
            self._settings.iperf_dir,
            self._settings.sensor_profile_dir,
            self._settings.runtime_dir / "archive",
            self._settings.runtime_dir / "conf",
            self._settings.public_dir,
            self._settings.web_cert_dir,
            self._settings.backup_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        self._settings.runtime_dir.chmod(0o700)
        self._settings.public_dir.chmod(0o755)
        # The NATS container mounts conf/ and certs/, the proxy mounts
        # web-certs/ and public/; the directories need the execute bit for the
        # container user. The files inside stay 0600/0644.
        (self._settings.runtime_dir / "conf").chmod(0o755)
        self._settings.cert_dir.chmod(0o755)
        self._settings.web_cert_dir.chmod(0o755)

    def publish_public_material(self) -> None:
        """runtime/public/: the CA and the health CGI, world-readable."""
        public = self._settings.public_dir
        public.mkdir(parents=True, exist_ok=True)
        public.chmod(0o755)

        ca = self._settings.cert_dir / "ca.pem"
        if not ca.is_file():
            raise RuntimeStateError(details="public CA not found")
        target = public / "nats-ca.pem"
        target.write_bytes(ca.read_bytes())
        target.chmod(0o644)

        cgi_source = self._settings.http_asset_dir / "cgi-bin" / "nats-health"
        if cgi_source.is_file():
            cgi_dir = public / "cgi-bin"
            cgi_dir.mkdir(parents=True, exist_ok=True)
            cgi_dir.chmod(0o755)
            cgi_target = cgi_dir / "nats-health"
            # The file is published read-only (0555); republishing has to
            # remove the previous copy first or its own mode blocks the write.
            cgi_target.unlink(missing_ok=True)
            cgi_target.write_bytes(cgi_source.read_bytes())
            cgi_target.chmod(0o555)

    def ensure_web_certificate(self) -> bool:
        """Issue the interface certificate if it is missing. Returns whether it did.

        This exists for the upgrade path. An installation set up before the
        reverse proxy used the platform's own CA has a complete runtime/ and no
        web certificate, so the proxy refuses to start - and the interface that
        would let an operator fix it is behind that proxy. Self-healing at
        startup turns a locked-out installation into a non-event.

        Silent when there is no CA yet: that is a fresh installation, and the
        setup job issues the certificate as part of initialisation.
        """
        certificate = self._settings.web_cert_dir / "web.pem"
        if certificate.is_file():
            return False
        if not (self._settings.cert_dir / "ca.pem").is_file():
            return False

        site = self._runtime.site_settings()
        if not site.web_fqdn:
            return False

        self._settings.web_cert_dir.mkdir(parents=True, exist_ok=True)
        self._settings.web_cert_dir.chmod(0o755)
        self._pki.issue_web_certificate(
            fqdn=site.web_fqdn, host_ip=site.nats_host_ip, archive=False
        )
        return True

    # --- Certificate renewal ------------------------------------------------

    def renew_server_certificate(self) -> None:
        """Renews both leaves: they share a CA, an expiry window and a renewal.

        Leaving the interface certificate behind would mean a browser warning
        appearing months after a renewal nobody remembers making.
        """
        site = self._runtime.site_settings()
        if not site.nats_fqdn:
            raise RuntimeStateError(details="NATS_FQDN is not configured")
        self._pki.issue_server_certificate(
            fqdn=site.nats_fqdn,
            host_ip=site.nats_host_ip,
            archive=True,
            overlay_address=_overlay_san(site),
        )
        self._pki.issue_web_certificate(
            fqdn=site.web_fqdn, host_ip=site.nats_host_ip, archive=True
        )

    # --- Backup -------------------------------------------------------------

    async def backup_jetstream(self) -> BackupResult:
        """Consistent JetStream backup, the way the shell did it.

        Stop NATS briefly, stream the volume out through the Docker API, gzip
        it into backups/, write the checksum beside it, start NATS again. The
        restart is in a finally block - a failed backup must not leave the
        backbone stopped.
        """
        backups = self._settings.backup_dir
        backups.mkdir(parents=True, exist_ok=True)
        backups.chmod(0o700)

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = backups / f"prtg-nats-jetstream-{stamp}.tar.gz"

        state = await self._docker.inspect(StackContainer.NATS)
        was_running = state.running

        try:
            if was_running:
                await self._docker.stop(StackContainer.NATS)

            digest = hashlib.sha256()
            size = 0
            with gzip.open(archive, "wb", compresslevel=6) as target:

                class _Tee:
                    def write(self, chunk: bytes) -> None:
                        nonlocal size
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)

                await self._docker.read_volume_archive(JETSTREAM_VOLUME, _Tee())
            archive.chmod(0o600)

            checksum = digest.hexdigest()
            checksum_file = Path(f"{archive}.sha256")
            checksum_file.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
            checksum_file.chmod(0o600)
        except BaseException:
            archive.unlink(missing_ok=True)
            Path(f"{archive}.sha256").unlink(missing_ok=True)
            raise
        finally:
            if was_running:
                await self._restart_after_backup()

        return BackupResult(archive=str(archive), sha256=checksum, size_bytes=size)

    async def _restart_after_backup(self) -> None:
        """Bring NATS back up, cancellation included.

        The restart is the half of this that must not be skipped: a backup
        interrupted by a shutdown or a cancelled job would otherwise leave the
        backbone stopped, and nothing starts it again - every probe loses its
        connection until somebody notices. Shielded so the cancellation
        travelling through this task does not reach the restart as well, and
        awaited once more afterwards so the container is up before the
        exception carries on.
        """
        restart = asyncio.ensure_future(self._start_nats())
        try:
            await asyncio.shield(restart)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await restart
            raise

    async def _start_nats(self) -> None:
        await self._docker.start(StackContainer.NATS)
        await self._docker.wait_healthy(StackContainer.NATS)

    # --- Runtime export -----------------------------------------------------

    # Excluded from the archive: previous archives (an export of an export
    # grows without bound) and the certificate snapshots, which are history
    # rather than state. Everything else goes in, keys included - this is the
    # copy that makes an installation restorable at all.
    EXPORT_EXCLUDES = ("backups", "archive")

    def export_runtime(self) -> BackupResult:
        """Archive runtime/ - the part of the installation nothing can rebuild.

        The JetStream backup covers message data. This covers the CA key, the
        certificates, the NATS accounts, the probe inventory, the management
        SSH key and the database. Losing it means re-enrolling every probe and
        re-pointing the PRTG core at a new CA.

        Written inside runtime/ so the volume keeps holding everything, and
        downloadable afterwards so it does not only exist there.
        """
        runtime = self._settings.runtime_dir
        if not runtime.is_dir():
            raise RuntimeStateError(
                params={"path": str(runtime)}, details="runtime directory is missing"
            )

        backups = self._settings.backup_dir
        backups.mkdir(parents=True, exist_ok=True)
        backups.chmod(0o700)

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = backups / f"prtg-nats-runtime-{stamp}.tar.gz"

        try:
            with tarfile.open(archive, "w:gz", compresslevel=6) as bundle:
                for entry in sorted(runtime.iterdir()):
                    if entry.name in self.EXPORT_EXCLUDES:
                        continue
                    bundle.add(entry, arcname=f"runtime/{entry.name}")
            archive.chmod(0o600)

            digest = hashlib.sha256()
            size = 0
            with archive.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)

            checksum = digest.hexdigest()
            checksum_file = Path(f"{archive}.sha256")
            checksum_file.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
            checksum_file.chmod(0o600)
        except BaseException:
            archive.unlink(missing_ok=True)
            Path(f"{archive}.sha256").unlink(missing_ok=True)
            raise

        return BackupResult(archive=str(archive), sha256=checksum, size_bytes=size)

    def list_backups(self) -> list[BackupFile]:
        """Newest first. Both kinds, because both matter for a restore."""
        backups = self._settings.backup_dir
        if not backups.is_dir():
            return []

        found: list[BackupFile] = []
        for path in backups.glob("*.tar.gz"):
            stat = path.stat()
            checksum_file = Path(f"{path}.sha256")
            checksum = None
            if checksum_file.is_file():
                checksum = checksum_file.read_text(encoding="utf-8").split(" ", 1)[0]
            found.append(
                BackupFile(
                    name=path.name,
                    kind="runtime" if "-runtime-" in path.name else "jetstream",
                    size_bytes=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                    sha256=checksum,
                )
            )
        return sorted(found, key=lambda item: item.created_at, reverse=True)

    def backup_path(self, name: str) -> Path:
        """Resolve a backup by name, refusing anything that escapes the folder."""
        if name != Path(name).name or name.startswith("."):
            raise NotFoundError.of("backup", name)
        path = self._settings.backup_dir / name
        if not path.is_file() or path.suffix != ".gz":
            raise NotFoundError.of("backup", name)
        return path

    # --- Account changes with server reload ---------------------------------

    async def create_account(self, username: str) -> str:
        # bcrypt at cost 11 is deliberate work; keep it off the event loop.
        password = await asyncio.to_thread(self._nats.create_account, username)
        await self._reload_server()
        return password

    async def rotate_account(self, username: str) -> str:
        password = await asyncio.to_thread(self._nats.rotate_account, username)
        await self._reload_server()
        return password

    async def delete_account(self, username: str) -> None:
        await asyncio.to_thread(self._nats.delete_account, username)
        await self._reload_server()

    async def _reload_server(self) -> None:
        """SIGHUP re-reads the configuration without dropping clients.

        Possible without a container recreate because compose mounts the
        runtime directory rather than the single file - the inode dance the
        shell had to do is gone.

        Verified afterwards, because NATS can accept the signal and refuse the
        reload: changing the server name is not reloadable, and the refusal
        goes to the container log where nobody is looking. The Docker API
        answers 200 either way. Left unchecked, creating an account looks like
        it worked while the probe that needs it gets "authorization violation"
        for as long as anyone cares to watch.
        """
        if not self._docker.available:
            # Without the socket the change activates on the next start; the
            # files are already correct. Said in the job log, not hidden.
            return
        state = await self._docker.inspect(StackContainer.NATS)
        if not state.running:
            return

        before = await self._config_load_time()
        await self._docker.reload_config(StackContainer.NATS)
        # NATS applies a reload synchronously, but the signal travels through
        # the daemon; a short wait avoids reading the old value back.
        after: str | None = None
        for _ in range(RELOAD_VERIFY_ATTEMPTS):
            await asyncio.sleep(RELOAD_VERIFY_INTERVAL)
            after = await self._config_load_time()
            if after is not None and after != before:
                return

        if before is None and after is None:
            # The monitoring endpoint answered nothing, before or after. That
            # says the reload could not be verified, not that it was refused -
            # reporting a failure here would turn every account change into an
            # error on an installation whose monitoring port is unreachable,
            # while the files and the signal were both fine.
            return

        raise NatsReloadRefusedError(
            details=(
                "NATS kept its previous configuration. The usual cause is a "
                "changed server name, which it cannot apply without a restart."
            )
        )

    async def _config_load_time(self) -> str | None:
        """When NATS last read its configuration, or None if it cannot say."""
        state = await NatsMonitoringClient(
            self._settings.nats_monitoring_url
        ).fetch_state()
        return state.config_load_time if state.available else None


def _overlay_san(site: SiteSettings) -> str | None:
    """The hub address, but only where there is an overlay.

    An installation that never turns it on has no reason to carry an address
    it does not answer on in its server certificate.
    """
    return site.overlay_hub_address if site.overlay_enabled else None
