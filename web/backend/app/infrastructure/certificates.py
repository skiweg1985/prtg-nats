"""Read the TLS material the stack generated.

The shell tooling asks openssl(1) for this. We use the cryptography library
instead: the same data without a subprocess per certificate, and a dashboard
that refreshes every minute should not fork twice for it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID, NameOID

from app.domain.enums import CertificateKind, CertificateStatus


@dataclass(frozen=True, slots=True)
class CertificateInfo:
    kind: CertificateKind
    path: str
    status: CertificateStatus
    subject: str | None = None
    issuer: str | None = None
    serial: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    # SHA-256 over the DER form - the value operators compare against a probe's
    # `ca_sha256` and against ./prtg-nats ca-info.
    sha256: str | None = None
    subject_alt_names: tuple[str, ...] = ()
    key_matches: bool | None = None

    @property
    def days_remaining(self) -> int | None:
        if self.not_after is None:
            return None
        return (self.not_after - datetime.now(UTC)).days


def fingerprint_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _attribute_text(value: str | bytes) -> str:
    """A name attribute is normally text, but X.509 permits raw bytes."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _format_name(name: x509.Name) -> str:
    parts: list[str] = []
    for oid, label in (
        (NameOID.COMMON_NAME, "CN"),
        (NameOID.ORGANIZATION_NAME, "O"),
        (NameOID.ORGANIZATIONAL_UNIT_NAME, "OU"),
        (NameOID.COUNTRY_NAME, "C"),
    ):
        values = name.get_attributes_for_oid(oid)
        parts.extend(f"{label}={_attribute_text(value.value)}" for value in values)
    return ", ".join(parts)


def _subject_alt_names(certificate: x509.Certificate) -> tuple[str, ...]:
    try:
        extension = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
    except x509.ExtensionNotFound:
        return ()
    san = extension.value
    assert isinstance(san, x509.SubjectAlternativeName)  # noqa: S101
    # An installation reached over a bare IP carries an iPAddress SAN; listing
    # only dNSName would show such a certificate as having no names at all.
    return tuple(
        str(value)
        for kind in (x509.DNSName, x509.IPAddress)
        for value in san.get_values_for_type(kind)
    )


def read_certificate(
    path: Path,
    kind: CertificateKind,
    *,
    key_path: Path | None = None,
    warning_days: int = 30,
) -> CertificateInfo:
    """Load one certificate, or report why it cannot be used.

    Never raises for a missing or broken file: this feeds a status page, and a
    status page that crashes when something is wrong has failed at its one job.
    """
    if not path.is_file():
        return CertificateInfo(
            kind=kind, path=str(path), status=CertificateStatus.MISSING
        )

    try:
        der_or_pem = path.read_bytes()
        certificate = x509.load_pem_x509_certificate(der_or_pem)
    except (OSError, ValueError):
        return CertificateInfo(
            kind=kind, path=str(path), status=CertificateStatus.MISSING
        )

    not_before = certificate.not_valid_before_utc
    not_after = certificate.not_valid_after_utc
    now = datetime.now(UTC)

    if now > not_after:
        status = CertificateStatus.EXPIRED
    elif (not_after - now).days <= warning_days:
        status = CertificateStatus.EXPIRING_SOON
    else:
        status = CertificateStatus.VALID

    key_matches: bool | None = None
    if key_path is not None:
        key_matches = _key_matches(certificate, key_path)
        if key_matches is False:
            status = CertificateStatus.MISMATCHED

    return CertificateInfo(
        kind=kind,
        path=str(path),
        status=status,
        subject=_format_name(certificate.subject),
        issuer=_format_name(certificate.issuer),
        serial=format(certificate.serial_number, "x"),
        not_before=not_before,
        not_after=not_after,
        sha256=fingerprint_sha256(certificate.public_bytes(serialization.Encoding.DER)),
        subject_alt_names=_subject_alt_names(certificate),
        key_matches=key_matches,
    )


def _key_matches(certificate: x509.Certificate, key_path: Path) -> bool | None:
    """Compare public keys, the way verify_certificate_pair() compares moduli."""
    try:
        key_bytes = key_path.read_bytes()
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except (OSError, ValueError, TypeError):
        return None

    public_form = serialization.PublicFormat.SubjectPublicKeyInfo
    encoding = serialization.Encoding.DER
    return private_key.public_key().public_bytes(
        encoding, public_form
    ) == certificate.public_key().public_bytes(encoding, public_form)


def fingerprint_of_pem(pem: str) -> str | None:
    try:
        certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError:
        return None
    return fingerprint_sha256(certificate.public_bytes(serialization.Encoding.DER))
