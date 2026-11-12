"""Check a certificate or key before it travels to a probe.

A variant's file slots are named after what belongs in them, and until now
anything could be put there. Two ways that went wrong in the field, both
within an hour of each other, and neither showed up before the probe had
measured: the certificate revocation list uploaded in place of the issuing
CA, because a Windows certificate names its CRL in the same extension block
as its issuer; and a private key that did not belong to the client
certificate beside it. The probe answered the first with "RADIUS certificate
rejected" and the second with "credentials rejected by RADIUS" - a question
about the network, asked because of a file on the server.

Everything here looks at bytes only. Whether the certificate is one a RADIUS
server will accept is the probe's business; whether it is a certificate at
all is ours.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

# What the blob turned out to be, in the wording of a refusal: "... but this
# is a certificate revocation list".
CERTIFICATE = "a certificate"
CRL = "a certificate revocation list"
PRIVATE_KEY = "a private key"
PUBLIC_KEY = "a public key"
CSR = "a certificate signing request"
PKCS12 = "a PKCS#12 bundle (.pfx)"
UNKNOWN = "neither a certificate nor a key"


def load_certificate(content: bytes) -> x509.Certificate | None:
    """The certificate in these bytes, PEM or DER, or None."""
    for load in (x509.load_pem_x509_certificate, x509.load_der_x509_certificate):
        try:
            return load(content)
        # S112: a parse error only means "not this format"; the next loader
        # gets its turn, and describe() says what it turned out to be.
        except Exception:  # noqa: S112
            continue
    return None


def load_private_key(
    content: bytes, passphrase: str | None = None
) -> PrivateKeyTypes | None:
    """The private key in these bytes, or None.

    A wrong passphrase is not told apart from a broken file here; the caller
    asks separately, because the two need different advice.
    """
    password = passphrase.encode("utf-8") if passphrase else None
    for load in (
        serialization.load_pem_private_key,
        serialization.load_der_private_key,
    ):
        try:
            return load(content, password=password)
        except Exception:  # noqa: S112 - see above
            continue
    return None


def is_encrypted_key(content: bytes) -> bool:
    """Whether the file is a private key that will not open without a word."""
    if b"ENCRYPTED PRIVATE KEY" in content or b"Proc-Type: 4,ENCRYPTED" in content:
        return True
    # An unencrypted PEM key opens with no password; if that fails while the
    # file looks like a PEM key, a passphrase is the likely reason.
    return b"PRIVATE KEY" in content and load_private_key(content) is None


def _parses(content: bytes, *loaders: object) -> bool:
    for load in loaders:
        try:
            load(content)  # type: ignore[operator]
        except Exception:  # noqa: S112 - see above
            continue
        return True
    return False


def describe(content: bytes) -> str:
    """What the bytes actually are, for a refusal that names it."""
    if load_certificate(content) is not None:
        return CERTIFICATE
    if _parses(content, x509.load_pem_x509_crl, x509.load_der_x509_crl):
        return CRL
    if _parses(content, x509.load_pem_x509_csr, x509.load_der_x509_csr):
        return CSR
    if b"PRIVATE KEY" in content or load_private_key(content) is not None:
        return PRIVATE_KEY
    if b"PUBLIC KEY" in content:
        return PUBLIC_KEY
    # PKCS#12 is DER and has no header to read, so it is recognised by the
    # shape of its first bytes rather than parsed - the passphrase for it is
    # not here anyway.
    if content[:1] == b"\x30" and b"PKCS" not in content and len(content) > 600:
        return PKCS12
    return UNKNOWN


def public_keys_match(certificate: x509.Certificate, key: PrivateKeyTypes) -> bool:
    """Whether the key is the one the certificate was made for."""
    encoding = serialization.Encoding.DER
    form = serialization.PublicFormat.SubjectPublicKeyInfo
    mine = key.public_key().public_bytes(encoding, form)
    theirs = certificate.public_key().public_bytes(encoding, form)
    return mine == theirs


def as_pem(certificate: x509.Certificate) -> bytes:
    """The certificate as PEM, whatever it arrived as.

    A Windows CA hands out its certificates as DER, and wpa_supplicant wants
    PEM. Converting here is the difference between a variant that works and
    one that fails on the probe for a reason nothing names.
    """
    return certificate.public_bytes(serialization.Encoding.PEM)
