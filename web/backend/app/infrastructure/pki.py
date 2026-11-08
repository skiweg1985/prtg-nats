"""Certificate and key generation, natively.

Replaces the openssl(1) calls in the retired init-runtime.sh and
renew-server-certificate.sh. Same shapes on purpose: CA RSA-4096 for ten
years, server RSA-3072 for 397 days with the configured host as its only
SAN, and an Ed25519 management key - a probe enrolled by the old tooling
stays valid.
"""

from __future__ import annotations

import ipaddress
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core.config import Settings
from app.core.errors import ConflictError, RuntimeStateError, ValidationFailedError
from app.infrastructure.runtime_files import RuntimeFileStore

CA_VALID_DAYS = 3650
SERVER_VALID_DAYS = 397
CA_KEY_BITS = 4096
SERVER_KEY_BITS = 3072
# ADR 0013: short enough that a probe which left without being unenrolled
# loses the channel on its own, long enough that renewal is routine.
PROBE_CLIENT_VALID_DAYS = 90
PROBE_IDENTITY_URN = "urn:prtg-nats:probe:"


def _host_san(host: str) -> x509.GeneralName:
    """The SAN entry a TLS client will actually match the host against.

    Go's crypto/tls - the NATS client - compares a literal address against
    iPAddress entries only and never falls back to dNSName. A bare IP written
    as a DNS entry therefore matches nothing, and the probe aborts the
    handshake with "bad certificate" while the chain itself is perfectly fine.
    """
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_bytes(content)


def _write_public(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o644)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


class Pki:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- Certificate authority ---------------------------------------------

    def create_ca(self, *, organization: str) -> None:
        ca_key_path = self._settings.private_dir / "ca-key.pem"
        ca_cert_path = self._settings.cert_dir / "ca.pem"
        if ca_key_path.exists() or ca_cert_path.exists():
            raise ConflictError(
                params={"resource": "ca"},
                details="a CA already exists; refusing to overwrite it",
            )

        key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_BITS)
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "PRTG Docker NATS CA"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            ]
        )
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=CA_VALID_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        _write_private(ca_key_path, _key_pem(key))
        _write_public(
            ca_cert_path, certificate.public_bytes(serialization.Encoding.PEM)
        )

    # --- Leaf certificates ---------------------------------------------------

    def _load_ca(self) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
        ca_key_path = self._settings.private_dir / "ca-key.pem"
        ca_cert_path = self._settings.cert_dir / "ca.pem"
        if not ca_key_path.is_file() or not ca_cert_path.is_file():
            raise RuntimeStateError(
                params={"path": str(ca_key_path)},
                details="CA state is missing; initialise the runtime first",
            )
        ca_key = serialization.load_pem_private_key(
            ca_key_path.read_bytes(), password=None
        )
        assert isinstance(ca_key, rsa.RSAPrivateKey)  # noqa: S101 - we created it
        return ca_key, x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    def _issue_leaf(
        self,
        *,
        common_name: str,
        sans: Sequence[x509.GeneralName],
        cert_path: Path,
        key_path: Path,
        archive: bool,
    ) -> None:
        ca_key, ca_cert = self._load_ca()

        if archive and cert_path.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive_dir = self._settings.runtime_dir / "archive" / stamp
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_dir.chmod(0o700)
            shutil.copy2(cert_path, archive_dir / cert_path.name)
            if key_path.exists():
                shutil.copy2(key_path, archive_dir / key_path.name)

        key = rsa.generate_private_key(public_exponent=65537, key_size=SERVER_KEY_BITS)
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
            )
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=SERVER_VALID_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_cert.public_key()  # type: ignore[arg-type]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        _write_private(key_path, _key_pem(key))
        _write_public(cert_path, certificate.public_bytes(serialization.Encoding.PEM))

    # --- Server certificate -------------------------------------------------

    def issue_server_certificate(
        self,
        *,
        fqdn: str,
        host_ip: str | None,
        archive: bool,
        overlay_address: str | None = None,
    ) -> None:
        """Issue (or renew) the NATS server certificate.

        With ``archive`` the previous pair is kept under runtime/archive/, the
        way the retired renew script did - a rollback is a copy, not a mystery.

        The host address is a SAN for the same reason the interface
        certificate carries one, only with more at stake: a probe usually sits
        in a network the FQDN of this installation does not resolve in - a
        customer site, a branch office, a segment with its own resolver. With
        the name alone such a probe has no way in at all, because reaching the
        server by address then fails on the certificate rather than on DNS.

        The overlay address is the same argument once more. Nothing needs it
        while the tunnel carries traffic to the ordinary address - which is how
        the fallback is built - but a probe pointed straight at the hub would
        otherwise fail on the certificate, and that is the way out if the
        routing approach does not survive a particular network.
        """
        sans: list[x509.GeneralName] = [_host_san(fqdn)]
        if host_ip and host_ip != fqdn:
            sans.append(_host_san(host_ip))
        if overlay_address and overlay_address not in {fqdn, host_ip}:
            sans.append(_host_san(overlay_address))

        self._issue_leaf(
            common_name=fqdn,
            sans=sans,
            cert_path=self._settings.cert_dir / "server.pem",
            key_path=self._settings.cert_dir / "server-key.pem",
            archive=archive,
        )
        self.verify_server_pair(fqdn=fqdn)

    # --- Interface certificate ----------------------------------------------

    def issue_web_certificate(
        self, *, fqdn: str, host_ip: str | None, archive: bool
    ) -> None:
        """Issue the certificate the reverse proxy serves the interface with.

        Signed by the same CA as everything else, and that is the whole point:
        an operator who compared the CA fingerprint once trusts the interface,
        the NATS server and the enrolment channel with that single decision.
        Caddy's own internal CA would be a second anchor nobody can verify -
        and the enrolment one-liner would need --insecure to get past it.

        The bare host address is a SAN because the interface answers on it too:
        an internal installation often has no DNS entry yet.
        """
        sans: list[x509.GeneralName] = [_host_san(fqdn)]
        if host_ip and host_ip != fqdn:
            sans.append(_host_san(host_ip))

        self._issue_leaf(
            common_name=fqdn,
            sans=sans,
            cert_path=self._settings.web_cert_dir / "web.pem",
            key_path=self._settings.web_cert_dir / "web-key.pem",
            archive=archive,
        )

    def verify_server_pair(self, *, fqdn: str) -> None:
        """The checks verify_certificate_pair() used to make, natively."""
        cert = x509.load_pem_x509_certificate(
            (self._settings.cert_dir / "server.pem").read_bytes()
        )
        ca = x509.load_pem_x509_certificate(
            (self._settings.cert_dir / "ca.pem").read_bytes()
        )
        cert.verify_directly_issued_by(ca)

        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        if _host_san(fqdn) not in san.value:
            raise RuntimeStateError(
                params={"fqdn": fqdn},
                details=f"server certificate SAN does not contain {fqdn}",
            )

        key = serialization.load_pem_private_key(
            (self._settings.cert_dir / "server-key.pem").read_bytes(), password=None
        )
        public = serialization.PublicFormat.SubjectPublicKeyInfo
        encoding = serialization.Encoding.DER
        if key.public_key().public_bytes(
            encoding, public
        ) != cert.public_key().public_bytes(encoding, public):
            raise RuntimeStateError(
                details="server certificate and private key do not match"
            )

    # --- Management SSH key -------------------------------------------------

    def ensure_management_key(self, *, fqdn: str) -> None:
        """Create the Ed25519 management key pair if it does not exist."""
        key_path = self._settings.ssh_key_path
        public_path = Path(f"{key_path}.pub")

        if key_path.exists() != public_path.exists():
            raise RuntimeStateError(
                params={"path": str(key_path)},
                details="incomplete management SSH key pair",
            )
        if not key_path.exists():
            key = ed25519.Ed25519PrivateKey.generate()
            _write_private(
                key_path,
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.OpenSSH,
                    serialization.NoEncryption(),
                ),
            )
            public_line = key.public_key().public_bytes(
                serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
            )
            _write_public(
                public_path,
                public_line + f" prtg-nats-mpp-admin@{fqdn}\n".encode(),
            )
        key_path.chmod(0o600)
        public_path.chmod(0o644)

        known_hosts = self._settings.ssh_known_hosts_path
        if not known_hosts.exists():
            _write_private(known_hosts, b"")

    def management_public_key(self) -> str:
        return Path(f"{self._settings.ssh_key_path}.pub").read_text(encoding="utf-8")

    # --- Probe client CA ----------------------------------------------------

    def ensure_probe_ca(self) -> bool:
        """Create the probe client CA if it is missing. Returns whether it did.

        Separate from the NATS CA on purpose (ADR 0013), so neither side's
        certificate opens the other's door. Created on demand rather than by
        initialisation: every installation set up before the return channel
        existed has a runtime without it, and the first startup or the first
        enrolment after the upgrade fills the gap.

        The public copy in web-certs/ is restored from the key's directory when
        only it went missing - it carries nothing the CA directory lacks.
        """
        key_path, cert_path = self._probe_ca_paths()
        if key_path.exists() != cert_path.exists():
            raise RuntimeStateError(
                params={"path": str(self._settings.probe_ca_dir)},
                details="incomplete probe client CA; restore it from a backup",
            )
        created = False
        if not key_path.exists():
            self._create_probe_ca(key_path, cert_path)
            created = True

        public = self._settings.probe_ca_public_path
        if (
            created
            or not public.is_file()
            or public.read_bytes() != (cert_path.read_bytes())
        ):
            public.parent.mkdir(parents=True, exist_ok=True)
            public.parent.chmod(0o755)
            _write_public(public, cert_path.read_bytes())
        return created

    def probe_ca_pem(self) -> str:
        self.ensure_probe_ca()
        return self._probe_ca_paths()[1].read_text(encoding="ascii")

    def sign_probe_csr(self, csr_pem: str, identity: str) -> str:
        """Sign a probe's CSR as a client certificate for ``identity``.

        The identity is the probe's NATS username (ADR 0011), taken from the
        inventory and never from the request: the subject the probe wrote into
        its CSR is ignored, and an identity that is not enrolled is refused.
        Only the public key is taken over, after the CSR has proved that its
        sender holds the matching private key.
        """
        # Raises NotFoundError for anything that is not an enrolled probe.
        RuntimeFileStore(self._settings).read_probe(identity)

        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise ValidationFailedError(
                params={"probe": identity}, details=f"unreadable CSR: {error}"
            ) from error
        if not csr.is_signature_valid:
            raise ValidationFailedError(
                params={"probe": identity},
                details="the CSR signature does not verify",
            )
        public_key = csr.public_key()
        if not isinstance(public_key, ec.EllipticCurvePublicKey | rsa.RSAPublicKey):
            raise ValidationFailedError(
                params={"probe": identity},
                details="the CSR carries a key type the channel does not accept",
            )

        self.ensure_probe_ca()
        ca_key, ca_cert = self._load_probe_ca()
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)])
            )
            .issuer_name(ca_cert.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=PROBE_CLIENT_VALID_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False,
            )
            # The identity once more, as a SAN. The proxy reads the CN, but a
            # strict RFC 5280 verifier refuses a leaf without any SAN, and a
            # NATS username is not always a valid DNS name - hence a URN.
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"{PROBE_IDENTITY_URN}{identity}")]
                ),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(public_key),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")

    def _probe_ca_paths(self) -> tuple[Path, Path]:
        directory = self._settings.probe_ca_dir
        return directory / "ca-key.pem", directory / "ca.pem"

    def _create_probe_ca(self, key_path: Path, cert_path: Path) -> None:
        # EC rather than the RSA-4096 of the NATS CA: nothing old has to
        # verify it, and the proxy checks a signature on every connection.
        key = ec.generate_private_key(ec.SECP384R1())
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "PRTG NATS probe client CA"),
                x509.NameAttribute(
                    NameOID.ORGANIZATION_NAME,
                    RuntimeFileStore(self._settings).site_settings().ca_organization,
                ),
            ]
        )
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=CA_VALID_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        # On a fresh volume this runs before initialisation has made private/,
        # so the parent gets its mode here rather than the umask's.
        for directory in (self._settings.private_dir, key_path.parent):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        _write_private(
            key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        _write_public(cert_path, certificate.public_bytes(serialization.Encoding.PEM))

    def _load_probe_ca(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        key_path, cert_path = self._probe_ca_paths()
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise RuntimeStateError(
                params={"path": str(key_path)},
                details="the probe client CA key is not the EC key it was made with",
            )
        return key, x509.load_pem_x509_certificate(cert_path.read_bytes())
