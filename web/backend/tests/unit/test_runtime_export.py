"""Getting the installation back out of the volume.

runtime/ no longer lives on a host path, so "copy the directory somewhere
safe" is not an answer any more. The export is what replaces it, and the bar
is simple: everything that cannot be rebuilt from the repository has to be in
the archive. A CA key that is not in there is a fleet of probes to re-enrol.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.pki import Pki
from app.services.provisioning import ProvisioningService

# What no fresh image can recreate. Losing any one of these means an
# installation that cannot come back.
IRREPLACEABLE = (
    "runtime/private/ca-key.pem",
    "runtime/certs/ca.pem",
    "runtime/certs/server.pem",
    "runtime/certs/server-key.pem",
    "runtime/web-certs/web.pem",
    "runtime/credentials/prtg-nats.env",
    "runtime/auth-users/prtg-nats.auth",
    "runtime/conf/nats-server.conf",
    "runtime/private/ssh/prtg-nats-mpp-admin",
)


@pytest.fixture
def provisioning(settings: Settings) -> ProvisioningService:
    return ProvisioningService(settings, DockerAdapter(settings))


def _initialised(settings: Settings, provisioning: ProvisioningService) -> None:
    provisioning.initialise_runtime()
    # A probe and an endpoint, so the inventory is not empty either.
    (settings.probe_dir / "mpp-berlin.env").write_text(
        "NATS_USERNAME=mpp-berlin\nSSH_HOST=probe.example.test\n", encoding="utf-8"
    )
    (settings.iperf_dir / "berlin.env").write_text(
        "IPERF_NAME=berlin\nIPERF_PASSWORD=secret\n", encoding="utf-8"
    )


def test_the_export_holds_everything_that_cannot_be_rebuilt(
    settings: Settings, provisioning: ProvisioningService, template_dir: Path
) -> None:
    _initialised(settings, provisioning)

    result = provisioning.export_runtime()

    with tarfile.open(result.archive, "r:gz") as bundle:
        names = set(bundle.getnames())

    missing = [name for name in IRREPLACEABLE if name not in names]
    assert not missing, f"the export would not restore this installation: {missing}"
    assert "runtime/probes/mpp-berlin.env" in names
    assert "runtime/iperf/berlin.env" in names
    assert Path(result.archive).stat().st_mode & 0o077 == 0


def test_the_export_does_not_contain_itself(
    settings: Settings, provisioning: ProvisioningService, template_dir: Path
) -> None:
    """backups/ is excluded, or every export doubles the size of the next one."""
    _initialised(settings, provisioning)
    provisioning.export_runtime()

    second = provisioning.export_runtime()
    with tarfile.open(second.archive, "r:gz") as bundle:
        names = bundle.getnames()

    assert not [name for name in names if name.startswith("runtime/backups")]
    assert not [name for name in names if name.startswith("runtime/archive")]


def test_the_checksum_beside_the_archive_matches_it(
    settings: Settings, provisioning: ProvisioningService, template_dir: Path
) -> None:
    """An archive nobody can verify is not a backup."""
    import hashlib

    _initialised(settings, provisioning)
    result = provisioning.export_runtime()

    archive = Path(result.archive)
    recorded = Path(f"{archive}.sha256").read_text(encoding="utf-8").split(" ", 1)[0]
    assert recorded == result.sha256
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result.sha256


def test_backups_are_listed_newest_first_with_their_kind(
    settings: Settings, provisioning: ProvisioningService, template_dir: Path
) -> None:
    _initialised(settings, provisioning)
    provisioning.export_runtime()

    listed = provisioning.list_backups()
    assert len(listed) == 1
    assert listed[0].kind == "runtime"
    assert listed[0].sha256 is not None
    assert listed[0].size_bytes > 0


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "/etc/passwd",
        ".hidden.tar.gz",
        "nats-server.conf",
    ],
)
def test_a_backup_name_cannot_escape_the_backup_folder(
    settings: Settings, provisioning: ProvisioningService, name: str
) -> None:
    """The name comes from a URL; treating it as a path would be a file read."""
    with pytest.raises(NotFoundError):
        provisioning.backup_path(name)


def test_a_web_certificate_is_issued_for_the_interface(
    settings: Settings, provisioning: ProvisioningService, template_dir: Path
) -> None:
    """Initialisation has to produce it, or the proxy has nothing to serve."""
    provisioning.initialise_runtime()

    assert (settings.web_cert_dir / "web.pem").is_file()
    assert (settings.web_cert_dir / "web-key.pem").is_file()
    Pki(settings).verify_server_pair(fqdn="nats.example.test")
