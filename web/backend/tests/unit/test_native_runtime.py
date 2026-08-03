"""The native replacements for the retired shell scripts.

The bar these have to clear: files the shell tooling wrote yesterday must be
readable, and files written here must be indistinguishable from the shell's.
"""

from __future__ import annotations

import re
from pathlib import Path

import bcrypt as bcrypt_lib
import pytest
from cryptography import x509

from app.core.config import Settings
from app.core.errors import ConflictError
from app.domain import probe_config
from app.infrastructure.nats_runtime import (
    BCRYPT_PATTERN,
    NatsRuntime,
    generate_password,
    hash_password,
)
from app.infrastructure.pki import Pki
from app.infrastructure.runtime_files import RuntimeFileStore


@pytest.fixture
def template_dir(project_dir: Path) -> Path:
    """The real templates from the repository, so rendering is tested against
    what actually ships."""
    config = project_dir / "config"
    config.mkdir(exist_ok=True)
    repo_config = Path(__file__).resolve().parents[4] / "config"
    for name in ("nats-server.conf.template", "mpprobe-config.yaml.template"):
        (config / name).write_text(
            (repo_config / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return config


# --- PKI ---------------------------------------------------------------------


def test_ca_and_server_certificate_match_the_shell_shapes(
    settings: Settings, template_dir: Path
) -> None:
    pki = Pki(settings)
    pki.create_ca(organization="Example Org")
    pki.issue_server_certificate(fqdn="nats.example.test", archive=False)

    ca = x509.load_pem_x509_certificate((settings.cert_dir / "ca.pem").read_bytes())
    assert "PRTG Docker NATS CA" in ca.subject.rfc4514_string()
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca

    server = x509.load_pem_x509_certificate(
        (settings.cert_dir / "server.pem").read_bytes()
    )
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert san.value.get_values_for_type(x509.DNSName) == ["nats.example.test"]
    # Issued by our CA, and the pair check passes.
    server.verify_directly_issued_by(ca)
    pki.verify_server_pair(fqdn="nats.example.test")

    # Private material is 0600, public is world-readable - the same modes the
    # shell set and the permission check verifies.
    assert (settings.private_dir / "ca-key.pem").stat().st_mode & 0o077 == 0
    assert (settings.cert_dir / "server-key.pem").stat().st_mode & 0o077 == 0


def test_a_bare_ip_host_becomes_an_ip_san(
    settings: Settings, template_dir: Path
) -> None:
    """The NATS client matches a literal address against iPAddress SANs only.

    Written as a dNSName it matches nothing, and every probe drops the
    connection with "bad certificate" even though the chain verifies.
    """
    pki = Pki(settings)
    pki.create_ca(organization="Example Org")
    pki.issue_server_certificate(fqdn="192.168.177.79", archive=False)

    server = x509.load_pem_x509_certificate(
        (settings.cert_dir / "server.pem").read_bytes()
    )
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert [str(value) for value in san.value.get_values_for_type(x509.IPAddress)] == [
        "192.168.177.79"
    ]
    assert san.value.get_values_for_type(x509.DNSName) == []
    pki.verify_server_pair(fqdn="192.168.177.79")


def test_a_second_ca_is_refused(settings: Settings) -> None:
    pki = Pki(settings)
    pki.create_ca(organization="Example Org")
    with pytest.raises(ConflictError):
        pki.create_ca(organization="Example Org")


def test_renewal_archives_the_previous_certificate(
    settings: Settings, template_dir: Path
) -> None:
    pki = Pki(settings)
    pki.create_ca(organization="Example Org")
    pki.issue_server_certificate(fqdn="nats.example.test", archive=False)
    first = (settings.cert_dir / "server.pem").read_bytes()

    pki.issue_server_certificate(fqdn="nats.example.test", archive=True)
    second = (settings.cert_dir / "server.pem").read_bytes()

    assert first != second
    archived = list((settings.runtime_dir / "archive").rglob("server.pem"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == first


def test_management_key_is_openssh_ed25519(settings: Settings) -> None:
    pki = Pki(settings)
    pki.ensure_management_key(fqdn="nats.example.test")

    public = Path(f"{settings.ssh_key_path}.pub").read_text(encoding="utf-8")
    assert public.startswith("ssh-ed25519 ")
    assert public.rstrip().endswith("prtg-nats-mpp-admin@nats.example.test")
    # Idempotent: a second call keeps the key.
    pki.ensure_management_key(fqdn="nats.example.test")
    assert public == Path(f"{settings.ssh_key_path}.pub").read_text(encoding="utf-8")


# --- NATS runtime ------------------------------------------------------------


def test_password_hash_matches_the_shape_nats_accepts() -> None:
    password = generate_password()
    assert re.match(r"^[0-9a-f]{64}$", password)

    hashed = hash_password(password)
    assert BCRYPT_PATTERN.match(hashed)
    assert hashed.startswith("$2a$11$")  # what `nats server passwd` produced
    assert bcrypt_lib.checkpw(password.encode(), hashed.encode())


def test_account_files_are_byte_compatible_with_the_shell(
    settings: Settings, template_dir: Path
) -> None:
    runtime = NatsRuntime(settings)
    password = runtime.create_account("mpp-berlin-01")

    credential = (settings.credential_dir / "mpp-berlin-01.env").read_text(
        encoding="utf-8"
    )
    assert credential.splitlines() == [
        "NATS_FQDN=nats.example.test",
        "NATS_PORT=23561",
        "NATS_USERNAME=mpp-berlin-01",
        f"NATS_PASSWORD={password}",
        "NATS_CA_PATH=/etc/paessler/mpprobe/certs/nats-docker-ca.pem",
    ]

    auth = (settings.auth_user_dir / "mpp-berlin-01.auth").read_text(encoding="utf-8")
    username, _, hashed = auth.strip().partition("\t")
    assert username == "mpp-berlin-01"
    assert BCRYPT_PATTERN.match(hashed)

    # And the store the platform reads sees the same account.
    assert RuntimeFileStore(settings).credential_exists("mpp-berlin-01")


def test_server_config_renders_every_account_and_the_derived_name(
    settings: Settings, template_dir: Path
) -> None:
    runtime = NatsRuntime(settings)
    runtime.create_account("prtg-nats")
    runtime.create_account("mpp-berlin-01")

    config = (settings.runtime_dir / "conf" / "nats-server.conf").read_text(
        encoding="utf-8"
    )
    assert 'user: "prtg-nats"' in config
    assert 'user: "mpp-berlin-01"' in config
    assert "@@" not in config  # every placeholder resolved
    assert 'server_name: "prtg-nats-nats"' in config  # derived from the FQDN
    assert "port: 23561" in config


def test_rotation_changes_the_password_and_keeps_a_snapshot(
    settings: Settings, template_dir: Path
) -> None:
    runtime = NatsRuntime(settings)
    first = runtime.create_account("mpp-berlin-01")
    second = runtime.rotate_account("mpp-berlin-01")

    assert first != second
    assert runtime.read_password("mpp-berlin-01") == second
    snapshots = list((settings.runtime_dir / "archive").glob("*user-rotate*"))
    assert snapshots, "a rotation must leave the previous state behind"


def test_deleting_the_last_account_is_refused(
    settings: Settings, template_dir: Path
) -> None:
    runtime = NatsRuntime(settings)
    runtime.create_account("prtg-nats")
    with pytest.raises(ConflictError):
        runtime.delete_account("prtg-nats")


def test_deleting_an_enrolled_probes_account_is_refused(
    settings: Settings, template_dir: Path, project_dir: Path
) -> None:
    from tests.conftest import write_probe_inventory

    runtime = NatsRuntime(settings)
    runtime.create_account("prtg-nats")
    runtime.create_account("mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-berlin-01")

    with pytest.raises(ConflictError):
        runtime.delete_account("mpp-berlin-01")


# --- Probe configuration -----------------------------------------------------


def test_probe_config_renders_the_real_template(
    settings: Settings, template_dir: Path
) -> None:
    values = probe_config.ProbeConfigValues(
        probe_id=probe_config.generate_probe_id(),
        access_key=probe_config.default_access_key("multi-platform-probe@berlin"),
        probe_name="multi-platform-probe@berlin",
        nats_host="nats.example.test",
        nats_port=23561,
        nats_user="mpp-berlin-01",
        nats_password="a" * 64,
    )
    rendered = probe_config.render_probe_config(
        template_dir / "mpprobe-config.yaml.template", values
    )

    assert f"id: {values.probe_id}" in rendered
    assert "url: tls://nats.example.test:23561" in rendered
    assert "user: mpp-berlin-01" in rendered
    assert "@@" not in rendered


def test_probe_config_refuses_invalid_values(template_dir: Path) -> None:
    from app.core.errors import ValidationFailedError

    values = probe_config.ProbeConfigValues(
        probe_id="not-a-uuid",
        access_key="x",  # too short
        probe_name="ok-name",
        nats_host="nats.example.test",
        nats_port=23561,
        nats_user="mpp",
        nats_password="pw",
    )
    with pytest.raises(ValidationFailedError) as excinfo:
        values.validate()
    assert "probe_id" in excinfo.value.fields
    assert "access_key" in excinfo.value.fields


def test_derived_names_follow_the_shell_rules() -> None:
    # Hostname: short form before the first dot.
    assert probe_config.host_label("berlin-probe.example.test") == "berlin-probe"
    # IP address: every octet kept, or nothing distinguishes probes in PRTG.
    assert probe_config.host_label("172.23.106.18") == "172-23-106-18"
    assert (
        probe_config.default_probe_name("172.23.106.18")
        == "multi-platform-probe@172-23-106-18"
    )
    # Access key: readable label first, full UUID after.
    key = probe_config.default_access_key("multi-platform-probe@site-north")
    assert key.startswith("site-north-")
    assert probe_config.ACCESS_KEY_PATTERN.match(key)


def test_verification_renders_config_check(
    settings: Settings, template_dir: Path
) -> None:
    """The offline check catches a corrupted auth entry."""
    from app.services.verification import StackVerification

    runtime = NatsRuntime(settings)
    runtime.create_account("prtg-nats")
    (settings.auth_user_dir / "broken.auth").write_text(
        "broken\tnot-a-hash\n", encoding="utf-8"
    )

    verification = StackVerification(settings)
    check = verification._check_server_config_renders()
    assert not check.ok
    assert "broken" in check.detail
