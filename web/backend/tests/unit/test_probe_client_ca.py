"""The CA that signs probe client certificates (ADR 0013).

Enrolment covers the happy path end to end in tests/api/test_enrollment.py.
What is held here is what no job exercises yet: renewal for a probe that
already has a certificate, the refusal for one that is not enrolled, and the
startup self-heal of the copy the proxy reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography import x509

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.infrastructure.pki import Pki
from app.services.provisioning import ProvisioningService
from tests.conftest import probe_csr, write_probe_inventory


def test_renewal_signs_a_fresh_csr_for_an_enrolled_probe(
    settings: Settings, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    pki = Pki(settings)
    first_csr, _ = probe_csr()
    second_csr, second_key = probe_csr()

    first = x509.load_pem_x509_certificate(
        pki.sign_probe_csr(first_csr, "mpp-berlin-01").encode()
    )
    renewed = x509.load_pem_x509_certificate(
        pki.sign_probe_csr(second_csr, "mpp-berlin-01").encode()
    )

    # Same CA, same identity, the new key.
    assert renewed.issuer == first.issuer
    assert renewed.subject == first.subject
    assert renewed.public_key() == second_key.public_key()
    assert renewed.serial_number != first.serial_number


def test_a_probe_that_is_not_enrolled_gets_no_certificate(
    settings: Settings, project_dir: Path
) -> None:
    csr, _ = probe_csr()
    with pytest.raises(NotFoundError):
        Pki(settings).sign_probe_csr(csr, "mpp-unknown")
    # Nor does a name that could escape the inventory directory.
    with pytest.raises(NotFoundError):
        Pki(settings).sign_probe_csr(csr, "../probes/mpp-unknown")


def test_the_ca_passes_strict_checks(settings: Settings, project_dir: Path) -> None:
    pki = Pki(settings)
    pki.ensure_probe_ca()
    ca = x509.load_pem_x509_certificate(settings.probe_ca_public_path.read_bytes())

    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and constraints.value.ca
    usage = ca.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.key_cert_sign
    ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)


def test_startup_creates_the_ca_once_and_restores_the_proxy_copy(
    settings: Settings, project_dir: Path
) -> None:
    provisioning = ProvisioningService(settings, docker=None)  # type: ignore[arg-type]
    assert provisioning.ensure_probe_ca() is True
    original = settings.probe_ca_public_path.read_bytes()

    assert provisioning.ensure_probe_ca() is False
    settings.probe_ca_public_path.unlink()
    assert provisioning.ensure_probe_ca() is False
    # The same CA, not a new one: a new CA would orphan every certificate.
    assert settings.probe_ca_public_path.read_bytes() == original
