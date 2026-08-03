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

    # --- Where the existing stack keeps its state ---------------------------
    # The project directory of the shell tooling. Everything else is derived
    # from it, so a single override relocates the whole installation.
    project_dir: Path = Path("/opt/prtg-nats-server")

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

    # --- Derived paths ------------------------------------------------------
    # These mirror libexec/common.sh. They are properties rather than settings
    # because the shell tooling derives them the same way; making them
    # configurable would create a second, silently diverging truth.

    @property
    def runtime_dir(self) -> Path:
        return self.project_dir / "runtime"

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
    def sensor_source_dir(self) -> Path:
        return self.project_dir / "sensors"

    @property
    def libexec_dir(self) -> Path:
        return self.project_dir / "libexec"

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
