"""NATS accounts and server configuration, natively.

Replaces the retired manage-users.sh and the rendering half of common.sh. The
file formats stay byte-compatible - runtime/credentials/USER.env,
runtime/auth-users/USER.auth and runtime/nats-server.conf - so everything that
reads them today keeps working.

bcrypt comes from the Python library instead of a throwaway nats-box
container, which removes the last reason this path needed Docker.
"""

from __future__ import annotations

import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import bcrypt

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, RuntimeStateError
from app.infrastructure.runtime_files import NATS_USERNAME_PATTERN

# Same shape the shell validated before trusting a hash.
BCRYPT_PATTERN = re.compile(r"^\$2[abxy]\$[0-9]{2}\$[./A-Za-z0-9]{53}$")
# The NATS server rejects cost factors above 11 by default ("2a too expensive")
# is not the issue - nats server passwd uses 11, and we match it.
BCRYPT_ROUNDS = 11

AUTH_PLACEHOLDER = "@@NATS_AUTH_USERS@@"


@dataclass(frozen=True, slots=True)
class NatsAccount:
    username: str
    is_shared: bool  # the core/legacy account, as opposed to a per-probe one
    has_auth_entry: bool
    credential_path: str


def generate_password() -> str:
    """64 hex characters, like `openssl rand -hex 32` before it."""
    return secrets.token_hex(32)


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(
        password.encode("ascii"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS, prefix=b"2a")
    ).decode("ascii")
    if not BCRYPT_PATTERN.match(hashed):
        raise RuntimeStateError(details="generated bcrypt hash has an unexpected shape")
    return hashed


class NatsRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- Reading ------------------------------------------------------------

    def list_accounts(self) -> list[NatsAccount]:
        accounts: list[NatsAccount] = []
        directory = self._settings.credential_dir
        if not directory.is_dir():
            return accounts
        for path in sorted(directory.glob("*.env")):
            username = path.stem
            if not NATS_USERNAME_PATTERN.match(username):
                continue
            accounts.append(
                NatsAccount(
                    username=username,
                    is_shared=username == "prtg-nats",
                    has_auth_entry=(
                        self._settings.auth_user_dir / f"{username}.auth"
                    ).is_file(),
                    credential_path=str(path),
                )
            )
        return accounts

    def account_exists(self, username: str) -> bool:
        if not NATS_USERNAME_PATTERN.match(username):
            return False
        return (self._settings.credential_dir / f"{username}.env").is_file()

    # --- File writers (same formats as the shell) ---------------------------

    def _write_credential_file(self, path: Path, username: str, password: str) -> None:
        content = (
            f"NATS_FQDN={self._fqdn()}\n"
            f"NATS_PORT={self._port()}\n"
            f"NATS_USERNAME={username}\n"
            f"NATS_PASSWORD={password}\n"
            "NATS_CA_PATH=/etc/paessler/mpprobe/certs/nats-docker-ca.pem\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(content, encoding="utf-8")

    def _write_auth_file(self, path: Path, username: str, password_hash: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(f"{username}\t{password_hash}\n", encoding="utf-8")

    # --- Server configuration ----------------------------------------------

    def render_server_config(self) -> str:
        """runtime/nats-server.conf from the template and every .auth file.

        Same validation as render_nats_config in the shell: a user name or a
        hash that does not match its pattern stops the render rather than
        producing a config NATS would reject at 3 a.m.
        """
        template_path = self._settings.template_dir / "nats-server.conf.template"
        if not template_path.is_file():
            raise RuntimeStateError(
                params={"path": str(template_path)},
                details="nats-server.conf.template is missing",
            )

        entries = []
        for auth_path in sorted(self._settings.auth_user_dir.glob("*.auth")):
            line = auth_path.read_text(encoding="utf-8").strip()
            username, _, password_hash = line.partition("\t")
            if not NATS_USERNAME_PATTERN.match(username):
                raise RuntimeStateError(
                    params={"path": str(auth_path)},
                    details=f"invalid NATS username in {auth_path.name}",
                )
            if not BCRYPT_PATTERN.match(password_hash):
                raise RuntimeStateError(
                    params={"path": str(auth_path)},
                    details=f"invalid bcrypt hash in {auth_path.name}",
                )
            entries.append((username, password_hash))
        if not entries:
            raise RuntimeStateError(details="no NATS auth users found")

        blocks = []
        for index, (username, password_hash) in enumerate(entries):
            comma = "," if index < len(entries) - 1 else ""
            blocks.append(
                "    {\n"
                f'      user: "{username}"\n'
                f'      password: "{password_hash}"\n'
                f"    }}{comma}"
            )

        server_name = f"prtg-nats-{_host_label(self._fqdn())}"
        rendered = []
        for line in template_path.read_text(encoding="utf-8").splitlines():
            if line.strip() == AUTH_PLACEHOLDER:
                rendered.extend(blocks)
            else:
                line = line.replace("@@NATS_PORT@@", str(self._port()))
                rendered.append(line.replace("@@SERVER_NAME@@", server_name))
        return "\n".join(rendered) + "\n"

    def write_server_config(self) -> None:
        config = self.render_server_config()
        path = self._settings.runtime_dir / "conf" / "nats-server.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o755)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(config, encoding="utf-8")

    # --- Account operations -------------------------------------------------

    def create_account(self, username: str) -> str:
        """Create the account files and rewrite the server config.

        Returns the cleartext password once - the caller hands it to whatever
        needs it now; afterwards the credential file is the only copy.
        """
        self._require_valid(username)
        if self.account_exists(username):
            raise ConflictError(
                params={"resource": "nats_account", "username": username},
                details="NATS account already exists",
            )
        self._snapshot(f"user-add-{username}")

        password = generate_password()
        self._write_credential_file(
            self._settings.credential_dir / f"{username}.env", username, password
        )
        self._write_auth_file(
            self._settings.auth_user_dir / f"{username}.auth",
            username,
            hash_password(password),
        )
        self.write_server_config()
        return password

    def rotate_account(self, username: str) -> str:
        self._require_valid(username)
        if not self.account_exists(username):
            raise NotFoundError.of("nats_account", username)
        self._snapshot(f"user-rotate-{username}")

        password = generate_password()
        self._write_credential_file(
            self._settings.credential_dir / f"{username}.env", username, password
        )
        self._write_auth_file(
            self._settings.auth_user_dir / f"{username}.auth",
            username,
            hash_password(password),
        )
        self.write_server_config()
        return password

    def delete_account(self, username: str) -> None:
        self._require_valid(username)
        if not self.account_exists(username):
            raise NotFoundError.of("nats_account", username)
        if (self._settings.probe_dir / f"{username}.env").is_file():
            # Same refusal the shell made: the probe inventory has to go first,
            # or a probe is left pointing at an account that no longer exists.
            raise ConflictError(
                params={"resource": "nats_account", "username": username},
                details="a probe is still enrolled for this account",
            )
        remaining = [
            account
            for account in self.list_accounts()
            if account.username != username and account.has_auth_entry
        ]
        if not remaining:
            raise ConflictError(
                params={"resource": "nats_account"},
                details="refusing to remove the last NATS account",
            )
        self._snapshot(f"user-delete-{username}")

        (self._settings.credential_dir / f"{username}.env").unlink(missing_ok=True)
        (self._settings.auth_user_dir / f"{username}.auth").unlink(missing_ok=True)
        self.write_server_config()

    def read_password(self, username: str) -> str:
        """The cleartext password, for the deployment path and for an audited
        reveal. Never returned by a list endpoint."""
        self._require_valid(username)
        path = self._settings.credential_dir / f"{username}.env"
        if not path.is_file():
            raise NotFoundError.of("nats_account", username)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("NATS_PASSWORD="):
                return line.partition("=")[2].strip()
        raise RuntimeStateError(
            params={"path": str(path)}, details="credential file has no password line"
        )

    # --- Helpers ------------------------------------------------------------

    def _snapshot(self, reason: str) -> None:
        """Copy auth-users/, credentials/ and the config aside before changing
        them - the rollback the shell tooling kept, preserved."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self._settings.runtime_dir / "archive" / f"{stamp}-{reason}"
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o700)
        for source in (self._settings.auth_user_dir, self._settings.credential_dir):
            if source.is_dir():
                shutil.copytree(source, target / source.name, dirs_exist_ok=True)
        config = self._settings.runtime_dir / "conf" / "nats-server.conf"
        if config.is_file():
            shutil.copy2(config, target / "nats-server.conf")

    @staticmethod
    def _require_valid(username: str) -> None:
        if not NATS_USERNAME_PATTERN.match(username):
            raise NotFoundError.of("nats_account", username)

    def _fqdn(self) -> str:
        from app.infrastructure.runtime_files import RuntimeFileStore

        site = RuntimeFileStore(self._settings).site_settings()
        if not site.nats_fqdn:
            raise RuntimeStateError(details="NATS_FQDN is not configured in .env")
        return site.nats_fqdn

    def _port(self) -> int:
        from app.infrastructure.runtime_files import RuntimeFileStore

        return RuntimeFileStore(self._settings).site_settings().nats_port


def _host_label(host: str) -> str:
    """Same derivation as mpp_host_label in the retired shell library."""
    if re.match(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$", host):
        host = host.replace(".", "-")
    else:
        host = host.split(".")[0]
    host = host.lower()
    host = re.sub(r"[^a-z0-9-]", "-", host).strip("-")
    return host or "probe"
