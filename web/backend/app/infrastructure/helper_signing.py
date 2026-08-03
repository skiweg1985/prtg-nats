"""The key that authorises a new probe helper.

Replacing the helper means putting new root code on a probe, and it travels
over the management channel - the same channel the helper itself serves. A
management key on its own must not be enough to do that, so the file is signed
here and verified on the probe against a public key that only ever arrives
over the bootstrap path.

Its own key rather than the NATS CA: signing code is not what a TLS authority
is for, and the CA has to stay rotatable without every probe losing the
ability to accept an update.

P-256 with SHA-256, because the probe verifies with ``openssl dgst -verify``.
That works on every OpenSSL a probe might carry, while verifying raw ed25519
needs a flag OpenSSL 1.1.1 does not have. The same pair is written by
ensure_helper_signing_key() in libexec/common.sh, and either side accepts what
the other wrote.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import Settings
from app.core.errors import RuntimeStateError

KEY_FILE = "helper-signing-key.pem"
PUBLIC_FILE = "helper-signing.pub"


class HelperSigner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def key_path(self) -> Path:
        return self._settings.private_dir / KEY_FILE

    @property
    def public_path(self) -> Path:
        return self._settings.private_dir / PUBLIC_FILE

    def public_key_pem(self) -> str:
        """What an enrolling probe is given as its trust anchor."""
        self._ensure_key()
        return self.public_path.read_text(encoding="utf-8")

    def sign(self, payload: bytes) -> str:
        """A signature over ``payload``, base64 on one line.

        One line because it travels as a single protocol argument, where a
        newline would end the request.
        """
        key = self._ensure_key()
        signature = key.sign(payload, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(signature).decode("ascii")

    # --- Key material -------------------------------------------------------

    def _ensure_key(self) -> ec.EllipticCurvePrivateKey:
        """Load the pair, creating it on first use.

        Created lazily rather than during setup: an installation from before
        signed updates has no such key, and demanding one up front would
        report a complete runtime as broken.
        """
        if self.key_path.is_file():
            return self._load_key()
        self._settings.private_dir.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        self.key_path.touch(mode=0o600, exist_ok=True)
        self.key_path.chmod(0o600)
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self._write_public(key)
        return key

    def _load_key(self) -> ec.EllipticCurvePrivateKey:
        key = serialization.load_pem_private_key(
            self.key_path.read_bytes(), password=None
        )
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise RuntimeStateError(
                params={"path": str(self.key_path)},
                details="the helper signing key is not an elliptic curve key",
            )
        # The public half is derived, so a missing one is written rather than
        # treated as damage - it is the file a probe was given, not a secret.
        if not self.public_path.is_file():
            self._write_public(key)
        return key

    def _write_public(self, key: ec.EllipticCurvePrivateKey) -> None:
        self.public_path.write_bytes(
            key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        self.public_path.chmod(0o644)
