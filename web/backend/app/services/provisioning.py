"""Initialise and maintain the server-side runtime, natively.

This is what remains of init-runtime.sh, renew-server-certificate.sh,
manage-users.sh, rotate-password.sh and backup-jetstream.sh once each is a
Python function with the same file formats and the same refusals.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.errors import ConflictError, RuntimeStateError
from app.infrastructure.docker import JETSTREAM_VOLUME, DockerAdapter, StackContainer
from app.infrastructure.nats_runtime import NatsRuntime
from app.infrastructure.pki import Pki
from app.infrastructure.runtime_files import RuntimeFileStore


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive: str
    sha256: str
    size_bytes: int


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
        self._pki.issue_server_certificate(fqdn=site.nats_fqdn, archive=False)
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
            self._settings.runtime_dir / "public",
        ):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        self._settings.runtime_dir.chmod(0o700)
        (self._settings.runtime_dir / "public").chmod(0o755)
        # The NATS container reads conf/ and certs/ through bind mounts; the
        # directories need the execute bit for the container user. The files
        # inside stay 0600/0644.
        (self._settings.runtime_dir / "conf").chmod(0o755)
        self._settings.cert_dir.chmod(0o755)

    def publish_public_material(self) -> None:
        """runtime/public/: the CA and the health CGI, world-readable."""
        public = self._settings.runtime_dir / "public"
        public.mkdir(parents=True, exist_ok=True)
        public.chmod(0o755)

        ca = self._settings.cert_dir / "ca.pem"
        if not ca.is_file():
            raise RuntimeStateError(details="public CA not found")
        target = public / "nats-ca.pem"
        target.write_bytes(ca.read_bytes())
        target.chmod(0o644)

        cgi_source = self._settings.project_dir / "http" / "cgi-bin" / "nats-health"
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

    # --- Certificate renewal ------------------------------------------------

    def renew_server_certificate(self) -> None:
        site = self._runtime.site_settings()
        if not site.nats_fqdn:
            raise RuntimeStateError(details="NATS_FQDN is not configured")
        self._pki.issue_server_certificate(fqdn=site.nats_fqdn, archive=True)

    # --- Backup -------------------------------------------------------------

    async def backup_jetstream(self) -> BackupResult:
        """Consistent JetStream backup, the way the shell did it.

        Stop NATS briefly, stream the volume out through the Docker API, gzip
        it into backups/, write the checksum beside it, start NATS again. The
        restart is in a finally block - a failed backup must not leave the
        backbone stopped.
        """
        backups = self._settings.project_dir / "backups"
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
                await self._docker.start(StackContainer.NATS)
                await self._docker.wait_healthy(StackContainer.NATS)

        return BackupResult(archive=str(archive), sha256=checksum, size_bytes=size)

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
        """
        if not self._docker.available:
            # Without the socket the change activates on the next start; the
            # files are already correct. Said in the job log, not hidden.
            return
        state = await self._docker.inspect(StackContainer.NATS)
        if state.running:
            await self._docker.reload_config(StackContainer.NATS)


def snapshot_paths(settings: Settings) -> list[str]:
    """The paths an operator should back up off-machine, for the docs page."""
    return [
        str(settings.runtime_dir),
        str(settings.project_dir / "backups"),
        str(settings.project_dir / ".env"),
    ]
