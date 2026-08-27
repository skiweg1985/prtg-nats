"""Runtime configuration.

Every setting has a default that works for local development. Deployments
override them through environment variables prefixed with ``PRTG_NATS_WEB_``
or through the ``.env`` file of the surrounding stack.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRTG_NATS_WEB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Identity -----------------------------------------------------------
    app_name: str = "PRTG-NATS"
    environment: str = "production"
    debug: bool = False

    # --- Updating this installation from its own checkout -------------------
    # The branch an update follows. Empty means the one the checkout is on,
    # which is the right default and was learned the hard way: a fixed "main"
    # looked like the careful choice - follow what was configured, not what
    # somebody moved the checkout to - and on an installation tracking dev it
    # produced "the branch main does not exist on origin" on the first look,
    # for an installation that was perfectly up to date.
    #
    # Set it to pin an installation to a branch on purpose. Left alone, the
    # checkout decides, which is what an operator who ran `git checkout dev`
    # already expects.
    update_branch: str = ""

    # --- Which commit this image was built from -----------------------------
    # Stamped in by the build, because a container has no checkout to ask.
    # Empty is a real answer and the honest one: an image built without the
    # argument cannot say what it contains, and guessing would make the
    # update page confidently wrong rather than usefully unsure.
    git_commit: str = ""
    git_ref: str = ""

    # --- Where the installation keeps its files -----------------------------
    # Two roots, because they have different lifetimes. Assets ship with the
    # image and never change at runtime; runtime state is written constantly
    # and is the only part worth backing up. Keeping them apart is what lets
    # the container mount one read-only image layer and one volume.
    #
    # project_dir keeps both under a single tree, which is what local
    # development and the tests want: one override configures everything.
    project_dir: Path = Path("/opt/prtg-nats-server")
    asset_dir_override: Path | None = Field(
        default=None, validation_alias="PRTG_NATS_WEB_ASSET_DIR"
    )
    runtime_dir_override: Path | None = Field(
        default=None, validation_alias="PRTG_NATS_WEB_RUNTIME_DIR"
    )

    # --- Database -----------------------------------------------------------
    # Lives inside runtime/ because it holds operational state that belongs to
    # the installation, not to the repository.
    database_url: str = ""

    # --- HTTP ---------------------------------------------------------------
    # Only paths a browser is served from. The reverse proxy terminates TLS,
    # so the application itself binds to loopback.
    host: str = "127.0.0.1"
    port: int = 8100
    cors_origins: list[str] = Field(default_factory=list)
    # Where the reverse proxy answers. Not used to listen - used to tell a
    # probe where to come back to, so it has to match what compose publishes.
    web_https_port: int = Field(default=443, validation_alias="WEB_HTTPS_PORT")

    # --- Sessions -----------------------------------------------------------
    session_cookie_name: str = "prtg_nats_session"
    session_lifetime_hours: int = 12
    session_idle_timeout_minutes: int = 60
    # Off for plain-HTTP development; the container image turns it on.
    session_cookie_secure: bool = True

    # --- Sign-in throttling -------------------------------------------------
    login_max_attempts: int = 5
    login_lockout_base_seconds: int = 5
    login_lockout_max_seconds: int = 900

    # --- Infrastructure adapters -------------------------------------------
    nats_monitoring_url: str = "http://127.0.0.1:8222"
    docker_socket: Path = Path("/var/run/docker.sock")
    ssh_connect_timeout_seconds: int = 10
    ssh_command_timeout_seconds: int = 120

    # --- Background work ----------------------------------------------------
    job_worker_count: int = 4
    inventory_sync_interval_seconds: int = 60
    # How often to ask the repository whether the branch has moved. Hourly by
    # default: an update is something an operator decides to do, not something
    # they need to hear about within the minute. 0 turns the check off, for an
    # installation that has no route to the repository and no wish to be told
    # about it on every pass.
    update_check_interval_seconds: int = 3600
    observed_state_stale_after_seconds: int = 300
    certificate_expiry_warning_days: int = 30

    # --- Development conveniences ------------------------------------------
    # Skips authentication and acts as a built-in administrator. Refused
    # outright when environment is "production" - see check_consistency().
    dev_auth_enabled: bool = False

    @field_validator("project_dir", mode="after")
    @classmethod
    def _absolute_project_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("asset_dir_override", "runtime_dir_override", mode="after")
    @classmethod
    def _absolute_override(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None

    # --- The two roots ------------------------------------------------------

    @property
    def asset_dir(self) -> Path:
        """Files that ship with the release: templates, sensors, scripts.

        Read-only at runtime. Nothing here is worth backing up - a fresh image
        brings it all back.
        """
        return self.asset_dir_override or self.project_dir

    @property
    def runtime_dir(self) -> Path:
        """State this installation owns: keys, credentials, inventory, database.

        The only thing worth backing up, and the reason the container mounts a
        volume rather than a host path.
        """
        return self.runtime_dir_override or self.project_dir / "runtime"

    # --- Derived paths ------------------------------------------------------

    @property
    def cert_dir(self) -> Path:
        return self.runtime_dir / "certs"

    @property
    def private_dir(self) -> Path:
        return self.runtime_dir / "private"

    @property
    def credential_dir(self) -> Path:
        return self.runtime_dir / "credentials"

    @property
    def auth_user_dir(self) -> Path:
        return self.runtime_dir / "auth-users"

    @property
    def probe_dir(self) -> Path:
        return self.runtime_dir / "probes"

    @property
    def iperf_dir(self) -> Path:
        return self.runtime_dir / "iperf"

    @property
    def sensor_profile_dir(self) -> Path:
        return self.runtime_dir / "sensor-profiles"

    @property
    def web_cert_dir(self) -> Path:
        """The interface certificate, kept out of cert_dir on purpose.

        NATS mounts cert_dir read-only. A private key sitting in there would be
        readable by the NATS container for no reason - and it is the key that
        would let someone impersonate the interface.
        """
        return self.runtime_dir / "web-certs"

    @property
    def backup_dir(self) -> Path:
        """Inside runtime/, so one volume holds everything a restore needs."""
        return self.runtime_dir / "backups"

    @property
    def public_dir(self) -> Path:
        """Served unauthenticated: the CA a probe fetches before it trusts us."""
        return self.runtime_dir / "public"

    @property
    def sensor_source_dir(self) -> Path:
        return self.asset_dir / "sensors"

    @property
    def libexec_dir(self) -> Path:
        return self.asset_dir / "libexec"

    @property
    def template_dir(self) -> Path:
        """nats-server.conf.template and mpprobe-config.yaml.template."""
        return self.asset_dir / "config"

    @property
    def http_asset_dir(self) -> Path:
        return self.asset_dir / "http"

    @property
    def ssh_key_path(self) -> Path:
        return self.private_dir / "ssh" / "prtg-nats-mpp-admin"

    @property
    def ssh_known_hosts_path(self) -> Path:
        return self.private_dir / "ssh" / "known_hosts"

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.runtime_dir / 'web.db'}"

    def check_consistency(self) -> None:
        """Refuse configurations that would be unsafe in production."""
        if self.dev_auth_enabled and self.environment == "production":
            raise RuntimeError(
                "PRTG_NATS_WEB_DEV_AUTH_ENABLED must not be set in production"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.check_consistency()
    return settings
