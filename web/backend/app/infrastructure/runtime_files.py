"""Read the state the shell tooling owns.

``runtime/`` is the source of truth for credentials, certificates, the probe
inventory and the iperf endpoints. The NATS container and every shell script
read those files directly, so the web platform reads them too instead of
keeping a copy that would drift within a week.

Everything here is read-only. Writes go through the domain services, which
either use the probe helper or the legacy adapter - never a stray open().
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.errors import NotFoundError, RuntimeStateError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Same rule as validate_nats_username in libexec/common.sh.
NATS_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Same rule as validate_iperf_name and validate_sensor_name.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# The only shape write_sensor_profile() in the probe helper accepts as a key.
PROFILE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file the way the shell tooling writes them.

    Deliberately not a shell parser: these files are generated, never sourced
    by us, and treating them as data means a stray backtick cannot become a
    command.
    """
    values: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    except PermissionError as exc:
        raise RuntimeStateError(
            params={"path": str(path)},
            details="permission denied; the service needs read access to runtime/",
        ) from exc

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True, slots=True)
class ProbeInventory:
    """One ``runtime/probes/USER.env`` plus its two sidecar files."""

    nats_username: str
    ssh_host: str
    ssh_port: int
    probe_id: str | None
    access_key_present: bool
    probe_name: str | None
    pending_transaction: str | None
    assigned_sensors: tuple[str, ...] = ()
    known_iperf_endpoints: tuple[str, ...] = ()

    @property
    def has_credentials(self) -> bool:
        return self.probe_id is not None


@dataclass(frozen=True, slots=True)
class IperfEndpointRecord:
    name: str
    host: str
    port: int
    username: str
    kind: str
    updated_at: datetime | None
    has_public_key: bool
    # Where the management channel to this endpoint answers. Absent in a record
    # the shell tooling wrote, which never opened one - 22 is right for every
    # endpoint that path produced, because nothing there connected at all.
    ssh_port: int = 22
    # Whether this platform set the host up and can still reach it. False for
    # an endpoint somebody else operates, which was registered here by hand:
    # its password is not ours to rotate and removing it here takes nothing off
    # that host. Absent in a record the shell tooling wrote, and read as true
    # there - that path only ever produced endpoints it had just set up.
    managed: bool = True


@dataclass(frozen=True, slots=True)
class IperfProfileMaterial:
    """What the credential profile of one measurement endpoint is made of.

    Named rather than a tuple because the profile carries five values now: the
    endpoint's address belongs in it too, so a variant describes a measurement
    path and not only the secret to walk it.
    """

    password: str
    public_key_b64: str
    host: str
    port: int
    username: str


@dataclass(frozen=True, slots=True)
class SensorProfileFile:
    """One certificate or key that belongs to a variant."""

    key: str
    filename: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SensorProfileRecord:
    """One variant of a sensor: its settings, credentials and files."""

    sensor: str
    name: str
    updated_at: datetime | None
    files: tuple[SensorProfileFile, ...] = ()
    # Which probes it is meant to be on. Read from the probe sidecars, since
    # that is where the assignment lives.
    probes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CertificateFile:
    path: Path
    exists: bool
    subject: str | None = None
    issuer: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    sha256: str | None = None
    sans: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SiteSettings:
    """Which NATS server this installation is, and who may reach it.

    Compose supplies these to the container as environment variables. The
    ``.env`` file beside the project is still read as a fallback so an
    installation created by the shell tooling keeps working unchanged; the
    environment wins where both are set.
    """

    nats_fqdn: str | None
    nats_port: int
    nats_host_ip: str | None
    ca_http_port: int
    ca_organization: str
    prtg_core_ip: str | None
    ssh_source_cidr: str | None
    # The same rule for iperf endpoints, and deliberately without the fallback
    # the line above has. A probe stands on the internal network and
    # NATS_HOST_IP is the address it sees us under; a measurement endpoint
    # often stands on a public one and sees us under the address we leave the
    # site with, which nothing here can derive. Left unset, every invitation
    # has to name it - a wrong guess would write a management key that is
    # valid from the wrong network, and there is no round trip that corrects
    # it afterwards.
    iperf_ssh_source_cidr: str | None = None
    # The interface usually answers on the same name as NATS; a deployment
    # that splits them sets WEB_FQDN, exactly as the proxy already expects.
    web_fqdn_override: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.nats_fqdn and self.nats_host_ip)

    @property
    def web_fqdn(self) -> str:
        return self.web_fqdn_override or self.nats_fqdn or ""

    @property
    def nats_endpoint(self) -> str | None:
        if not self.nats_fqdn:
            return None
        return f"tls://{self.nats_fqdn}:{self.nats_port}"


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    """Whether the shell stack was ever set up in this directory."""

    state: str  # "missing" | "partial" | "complete"
    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = field(default_factory=tuple)


class RuntimeFileStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- Site settings ------------------------------------------------------

    def site_settings(self) -> SiteSettings:
        # File first, environment on top: a value set in compose overrides the
        # same key in a leftover .env rather than silently losing to it.
        values = read_env_file(self._settings.project_dir / ".env")
        for key in (
            "NATS_FQDN",
            "NATS_PORT",
            "NATS_HOST_IP",
            "CA_HTTP_PORT",
            "CA_ORGANIZATION",
            "PRTG_CORE_IP",
            "MPP_SSH_SOURCE_CIDR",
            "IPERF_SSH_SOURCE_CIDR",
            "WEB_FQDN",
        ):
            from_environment = os.environ.get(key)
            if from_environment:
                values[key] = from_environment

        host_ip = values.get("NATS_HOST_IP") or None
        return SiteSettings(
            nats_fqdn=values.get("NATS_FQDN") or None,
            nats_port=_as_int(values.get("NATS_PORT"), 23561),
            nats_host_ip=host_ip,
            ca_http_port=_as_int(values.get("CA_HTTP_PORT"), 80),
            ca_organization=values.get("CA_ORGANIZATION") or "PRTG NATS",
            prtg_core_ip=values.get("PRTG_CORE_IP") or None,
            ssh_source_cidr=(
                values.get("MPP_SSH_SOURCE_CIDR")
                or (f"{host_ip}/32" if host_ip else None)
            ),
            iperf_ssh_source_cidr=values.get("IPERF_SSH_SOURCE_CIDR") or None,
            web_fqdn_override=values.get("WEB_FQDN") or None,
        )

    # --- Runtime completeness ----------------------------------------------

    def health(self) -> RuntimeHealth:
        """Mirrors runtime_state() in the prtg-nats entry point."""
        expected = {
            "conf/nats-server.conf": self._settings.runtime_dir
            / "conf"
            / "nats-server.conf",
            "ca.pem": self._settings.cert_dir / "ca.pem",
            "server.pem": self._settings.cert_dir / "server.pem",
            "server-key.pem": self._settings.cert_dir / "server-key.pem",
            "ca-key.pem": self._settings.private_dir / "ca-key.pem",
            "prtg-nats.env": self._settings.credential_dir / "prtg-nats.env",
        }
        present = tuple(name for name, path in expected.items() if path.is_file())
        missing = tuple(name for name in expected if name not in present)
        if not present:
            state = "missing"
        elif not missing:
            state = "complete"
        else:
            state = "partial"
        return RuntimeHealth(state=state, present=present, missing=missing)

    # --- Probes -------------------------------------------------------------

    def list_probe_usernames(self) -> list[str]:
        directory = self._settings.probe_dir
        if not directory.is_dir():
            return []
        names = [
            path.stem
            for path in sorted(directory.glob("*.env"))
            if NATS_USERNAME_PATTERN.match(path.stem)
        ]
        return names

    def read_probe(self, username: str) -> ProbeInventory:
        if not NATS_USERNAME_PATTERN.match(username):
            raise NotFoundError.of("probe", username)
        path = self._settings.probe_dir / f"{username}.env"
        if not path.is_file():
            raise NotFoundError.of("probe", username)
        values = read_env_file(path)

        return ProbeInventory(
            nats_username=values.get("NATS_USERNAME", username),
            ssh_host=values.get("SSH_HOST", ""),
            ssh_port=_as_int(values.get("SSH_PORT"), 22),
            probe_id=values.get("PROBE_ID") or None,
            # The access key is a PRTG secret. Its presence is inventory, its
            # value is not - it is fetched explicitly and audited when shown.
            access_key_present=bool(values.get("ACCESS_KEY")),
            probe_name=values.get("PROBE_NAME") or None,
            pending_transaction=values.get("PENDING_TRANSACTION") or None,
            assigned_sensors=self._read_lines(
                self._settings.probe_dir / f"{username}.sensors"
            ),
            known_iperf_endpoints=self._read_lines(
                self._settings.probe_dir / f"{username}.iperf"
            ),
        )

    def probe_username_for_host(
        self, host: str, *, excluding: str | None = None
    ) -> str | None:
        """Which enrolled probe already claims this address, if any.

        Two inventory entries for one host share a management access, because
        that access belongs to the host and not to the entry. Retiring either
        one revokes it, and the survivor goes unreachable while still happily
        connected to NATS - observed exactly that way.

        Compared as written, lower-cased. A name and the address behind it are
        not recognised as the same host: resolving one to the other would be a
        DNS lookup whose answer can change between the check and the write.
        """
        wanted = host.strip().lower()
        if not wanted:
            return None
        for username in self.list_probe_usernames():
            if username == excluding:
                continue
            try:
                inventory = self.read_probe(username)
            except NotFoundError:
                continue
            if inventory.ssh_host.strip().lower() == wanted:
                return username
        return None

    def read_all_probes(self) -> list[ProbeInventory]:
        probes = []
        for username in self.list_probe_usernames():
            try:
                probes.append(self.read_probe(username))
            except (NotFoundError, RuntimeStateError):
                logger.warning(
                    "skipping unreadable probe inventory", extra={"probe": username}
                )
        return probes

    def read_access_key(self, username: str) -> str | None:
        """Deliberately separate from read_probe(): callers must ask for it."""
        if not NATS_USERNAME_PATTERN.match(username):
            raise NotFoundError.of("probe", username)
        values = read_env_file(self._settings.probe_dir / f"{username}.env")
        return values.get("ACCESS_KEY") or None

    # --- NATS accounts ------------------------------------------------------

    def list_nats_usernames(self) -> list[str]:
        directory = self._settings.credential_dir
        if not directory.is_dir():
            return []
        return [
            path.stem
            for path in sorted(directory.glob("*.env"))
            if NATS_USERNAME_PATTERN.match(path.stem)
        ]

    def credential_password(self, username: str) -> str | None:
        """The NATS password in clear.

        Only the deployment path calls this, and only to hand the value
        straight to the probe helper over the encrypted channel. It is never
        returned by an endpoint and never logged.
        """
        if not NATS_USERNAME_PATTERN.match(username):
            raise NotFoundError.of("credential", username)
        values = read_env_file(self._settings.credential_dir / f"{username}.env")
        return values.get("NATS_PASSWORD") or None

    def credential_exists(self, username: str) -> bool:
        if not NATS_USERNAME_PATTERN.match(username):
            return False
        return (self._settings.credential_dir / f"{username}.env").is_file()

    # --- iperf endpoints ----------------------------------------------------

    def list_iperf_endpoints(self) -> list[IperfEndpointRecord]:
        directory = self._settings.iperf_dir
        if not directory.is_dir():
            return []
        endpoints = []
        for path in sorted(directory.glob("*.env")):
            if not NAME_PATTERN.match(path.stem):
                continue
            values = read_env_file(path)
            endpoints.append(
                IperfEndpointRecord(
                    name=values.get("IPERF_NAME", path.stem),
                    host=values.get("IPERF_HOST", ""),
                    port=_as_int(values.get("IPERF_PORT"), 5201),
                    username=values.get("IPERF_USERNAME", ""),
                    kind=values.get("IPERF_KIND", "iperf3"),
                    updated_at=_as_datetime(values.get("IPERF_UPDATED")),
                    has_public_key=(directory / f"{path.stem}.pem").is_file(),
                    managed=_as_bool(values.get("IPERF_MANAGED"), default=True),
                    ssh_port=_as_int(values.get("IPERF_SSH_PORT"), 22),
                )
            )
        return endpoints

    # --- Certificates -------------------------------------------------------

    def ca_pem(self) -> str:
        path = self._settings.cert_dir / "ca.pem"
        if not path.is_file():
            raise RuntimeStateError(
                params={"path": str(path)}, details="public CA certificate not found"
            )
        return path.read_text(encoding="utf-8")

    def certificate_path(self, kind: str) -> Path:
        return self._settings.cert_dir / ("ca.pem" if kind == "ca" else "server.pem")

    # --- Writers ------------------------------------------------------------
    # The platform is a full peer of the retired management scripts now, so it
    # writes the same files with the same modes. Only these paths are written;
    # certificates and credentials go through Pki and NatsRuntime.

    def write_probe_inventory(
        self,
        *,
        nats_username: str,
        ssh_host: str,
        ssh_port: int = 22,
        probe_id: str,
        access_key: str,
        probe_name: str,
        pending_transaction: str = "",
    ) -> None:
        if not NATS_USERNAME_PATTERN.match(nats_username):
            raise NotFoundError.of("probe", nats_username)
        path = self._settings.probe_dir / f"{nats_username}.env"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"NATS_USERNAME={nats_username}\n"
            f"SSH_HOST={ssh_host}\n"
            f"SSH_PORT={ssh_port}\n"
            f"PENDING_TRANSACTION={pending_transaction}\n"
            f"PROBE_ID={probe_id}\n"
            f"ACCESS_KEY={access_key}\n"
            f"PROBE_NAME={probe_name}\n"
        )
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(content, encoding="utf-8")

    def write_iperf_record(
        self,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        password: str,
        public_key_pem: str | None = None,
        managed: bool = True,
        ssh_port: int = 22,
        kind: str = "iperf3",
    ) -> None:
        """The endpoint's record and its public key, in the format
        ``./prtg-nats iperf-server`` has always written.

        Byte-compatible on purpose: the shell tooling still reads these files,
        and an endpoint set up from the browser has to be one the command line
        can deploy, show and revoke without knowing where it came from.

        The public key is optional because a rotation does not produce a new
        one - the key pair on the endpoint stays untouched, and overwriting the
        stored copy with nothing would cost every probe its ability to encrypt
        the credentials it sends.
        """
        if not NAME_PATTERN.match(name):
            raise NotFoundError.of("iperf_endpoint", name)
        directory = self._settings.iperf_dir
        directory.mkdir(parents=True, exist_ok=True)
        record = directory / f"{name}.env"
        content = (
            f"IPERF_NAME={name}\n"
            f"IPERF_KIND={kind}\n"
            f"IPERF_HOST={host}\n"
            f"IPERF_PORT={port}\n"
            f"IPERF_USERNAME={username}\n"
            f"IPERF_PASSWORD={password}\n"
            f"IPERF_MANAGED={'true' if managed else 'false'}\n"
            f"IPERF_SSH_PORT={ssh_port}\n"
            f"IPERF_UPDATED={datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        )
        record.touch(mode=0o600, exist_ok=True)
        record.chmod(0o600)
        record.write_text(content, encoding="utf-8")

        if public_key_pem is None:
            return
        key_file = directory / f"{name}.pem"
        key_file.touch(mode=0o600, exist_ok=True)
        key_file.chmod(0o600)
        key_file.write_text(public_key_pem, encoding="utf-8")

    def remove_iperf_record(self, name: str) -> None:
        """Forget an endpoint here. What runs on that host is not touched by
        this - removing the service is a request over its own channel."""
        if not NAME_PATTERN.match(name):
            raise NotFoundError.of("iperf_endpoint", name)
        directory = self._settings.iperf_dir
        (directory / f"{name}.env").unlink(missing_ok=True)
        (directory / f"{name}.pem").unlink(missing_ok=True)

    def iperf_endpoint_exists(self, name: str) -> bool:
        if not NAME_PATTERN.match(name):
            return False
        return (self._settings.iperf_dir / f"{name}.env").is_file()

    def _sidecar(self, nats_username: str, suffix: str) -> Path:
        if not NATS_USERNAME_PATTERN.match(nats_username):
            raise NotFoundError.of("probe", nats_username)
        return self._settings.probe_dir / f"{nats_username}.{suffix}"

    def _remember_line(self, path: Path, value: str) -> None:
        lines = set(self._read_lines(path))
        if value in lines:
            return
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{value}\n")

    def _forget_line(self, path: Path, value: str) -> None:
        lines = [line for line in self._read_lines(path) if line != value]
        if not path.exists():
            return
        if lines:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            path.chmod(0o600)
        else:
            path.unlink()

    def remember_sensor(self, nats_username: str, sensor: str) -> None:
        """Record a deployed sensor in USER.sensors - the bookkeeping the CLI
        views and the desired-state fallback both read."""
        self._remember_line(self._sidecar(nats_username, "sensors"), sensor)

    def forget_sensor(self, nats_username: str, sensor: str) -> None:
        self._forget_line(self._sidecar(nats_username, "sensors"), sensor)

    def remember_iperf(self, nats_username: str, endpoint: str) -> None:
        self._remember_line(self._sidecar(nats_username, "iperf"), endpoint)

    def forget_iperf(self, nats_username: str, endpoint: str) -> None:
        self._forget_line(self._sidecar(nats_username, "iperf"), endpoint)

    def assigned_iperf(self, nats_username: str) -> tuple[str, ...]:
        """The endpoints one probe is meant to measure against.

        The counterpart of assigned_profiles for the endpoints, and read for
        the same reason: a rollout deploys what a probe is meant to hold, not
        everything the installation happens to know.
        """
        return self._read_lines(self._sidecar(nats_username, "iperf"))

    def remove_probe(self, nats_username: str) -> None:
        """Delete the inventory and its sidecars - the unenroll bookkeeping."""
        for suffix in ("env", "sensors", "iperf", "profiles"):
            self._sidecar(nats_username, suffix).unlink(missing_ok=True)

    # --- Sensor variants ----------------------------------------------------
    #
    # A variant is a profile file plus the certificates and keys that belong
    # to it, kept centrally under runtime/sensor-profiles/ - in the same
    # protected, git-ignored area as the NATS passwords, and in the byte format
    # "./prtg-nats sensor profile" has always written, so a variant filled in
    # from the interface is one the command line can deploy again.

    def _profile_dir(self, sensor: str) -> Path:
        if not NAME_PATTERN.match(sensor):
            raise NotFoundError.of("sensor", sensor)
        return self._settings.sensor_profile_dir / sensor

    def _profile_path(self, sensor: str, profile: str) -> Path:
        if not NAME_PATTERN.match(profile):
            raise NotFoundError.of("sensor_profile", profile)
        return self._profile_dir(sensor) / f"{profile}.env"

    def _profile_file_dir(self, sensor: str, profile: str) -> Path:
        if not NAME_PATTERN.match(profile):
            raise NotFoundError.of("sensor_profile", profile)
        return self._profile_dir(sensor) / "files" / profile

    def write_sensor_profile(
        self, sensor: str, profile: str, values: dict[str, str]
    ) -> None:
        """Store one variant.

        The probe helper accepts a profile as comments and ``KEY=VALUE`` lines
        and nothing else, so a value carrying a line break is refused here
        rather than on the probe: the same rejection, but with a field name
        attached to it.
        """
        path = self._profile_path(sensor, profile)
        lines = [
            "# Written by the PRTG-NATS web platform.",
            f"# Variant {profile} of {sensor}.",
        ]
        for key in sorted(values):
            value = values[key]
            if not PROFILE_KEY_PATTERN.match(key):
                raise RuntimeStateError(
                    params={"sensor": sensor, "key": key},
                    details="a profile key is upper case, digits and underscores",
                )
            if any(character in value for character in "\n\r\x00"):
                raise RuntimeStateError(
                    params={"sensor": sensor, "key": key},
                    details="a profile value cannot contain a line break",
                )
            lines.append(f"{key}={value}")

        path.parent.mkdir(parents=True, exist_ok=True)
        # 0700 on the directories as well: the file mode alone would still let
        # anything list which variants exist and for which sites.
        self._settings.sensor_profile_dir.chmod(0o700)
        path.parent.chmod(0o700)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def read_sensor_profile(self, sensor: str, profile: str) -> dict[str, str]:
        path = self._profile_path(sensor, profile)
        if not path.is_file():
            raise NotFoundError.of("sensor_profile", f"{sensor}/{profile}")
        return read_env_file(path)

    def sensor_profile_exists(self, sensor: str, profile: str) -> bool:
        return self._profile_path(sensor, profile).is_file()

    def sensor_profile_content(self, sensor: str, profile: str) -> str:
        """The file as it goes to the probe, byte for byte."""
        path = self._profile_path(sensor, profile)
        if not path.is_file():
            raise NotFoundError.of("sensor_profile", f"{sensor}/{profile}")
        return path.read_text(encoding="utf-8")

    def list_sensor_profiles(self, sensor: str) -> list[SensorProfileRecord]:
        directory = self._profile_dir(sensor)
        if not directory.is_dir():
            return []
        assignments = self._profile_assignments()
        records = []
        for path in sorted(directory.glob("*.env")):
            name = path.stem
            if not NAME_PATTERN.match(name):
                continue
            records.append(
                SensorProfileRecord(
                    sensor=sensor,
                    name=name,
                    updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    files=tuple(self.list_sensor_profile_files(sensor, name)),
                    probes=tuple(sorted(assignments.get((sensor, name), ()))),
                )
            )
        return records

    def remove_sensor_profile(self, sensor: str, profile: str) -> None:
        """Forget a variant here. What sits on the probes is a separate
        request, which the job makes before this."""
        self._profile_path(sensor, profile).unlink(missing_ok=True)
        directory = self._profile_file_dir(sensor, profile)
        if directory.is_dir():
            for entry in directory.iterdir():
                entry.unlink()
            directory.rmdir()

    def write_sensor_profile_file(
        self, sensor: str, profile: str, key: str, filename: str, payload: bytes
    ) -> None:
        """Store a certificate or key of one variant.

        Replaces whatever was under this key before, extension included: the
        path is written into the profile, and two files for one key would leave
        the profile pointing at the one that is no longer meant.
        """
        if not PROFILE_KEY_PATTERN.match(key):
            raise NotFoundError.of("sensor_profile_file", key)
        if "/" in filename or filename.startswith("."):
            raise NotFoundError.of("sensor_profile_file", filename)
        directory = self._profile_file_dir(sensor, profile)
        directory.mkdir(parents=True, exist_ok=True)
        self._settings.sensor_profile_dir.chmod(0o700)
        for parent in (directory.parent.parent, directory.parent, directory):
            parent.chmod(0o700)
        for existing in directory.glob(f"{key}.*"):
            existing.unlink()
        path = directory / filename
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_bytes(payload)

    def list_sensor_profile_files(
        self, sensor: str, profile: str
    ) -> list[SensorProfileFile]:
        directory = self._profile_file_dir(sensor, profile)
        if not directory.is_dir():
            return []
        files = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            files.append(
                SensorProfileFile(
                    key=path.stem,
                    filename=path.name,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        return files

    def read_sensor_profile_file(self, sensor: str, profile: str, key: str) -> bytes:
        if not PROFILE_KEY_PATTERN.match(key):
            raise NotFoundError.of("sensor_profile_file", key)
        directory = self._profile_file_dir(sensor, profile)
        for path in sorted(directory.glob(f"{key}.*")):
            return path.read_bytes()
        raise NotFoundError.of("sensor_profile_file", f"{sensor}/{profile}/{key}")

    def remove_sensor_profile_file(self, sensor: str, profile: str, key: str) -> None:
        if not PROFILE_KEY_PATTERN.match(key):
            raise NotFoundError.of("sensor_profile_file", key)
        for path in self._profile_file_dir(sensor, profile).glob(f"{key}.*"):
            path.unlink()

    # --- Which variant belongs on which probe -------------------------------
    #
    # In runtime/probes/USER.profiles, one "sensor/variant" per line. Desired
    # state rather than bookkeeping, the same way USER.sensors is: it is what
    # a rollout reads to know which variants have to travel with the sensor.

    def assign_profile(self, nats_username: str, sensor: str, profile: str) -> None:
        self._remember_line(
            self._sidecar(nats_username, "profiles"), f"{sensor}/{profile}"
        )

    def unassign_profile(self, nats_username: str, sensor: str, profile: str) -> None:
        self._forget_line(
            self._sidecar(nats_username, "profiles"), f"{sensor}/{profile}"
        )

    def assigned_profiles(
        self, nats_username: str, sensor: str | None = None
    ) -> tuple[str, ...]:
        """The variants one probe is meant to hold, for one sensor or all."""
        entries = self._read_lines(self._sidecar(nats_username, "profiles"))
        pairs = [entry.partition("/") for entry in entries]
        return tuple(
            profile
            for name, separator, profile in pairs
            if separator and profile and (sensor is None or name == sensor)
        )

    def _profile_assignments(self) -> dict[tuple[str, str], list[str]]:
        """Every assignment, keyed by variant - the reverse of the sidecars."""
        assignments: dict[tuple[str, str], list[str]] = {}
        for username in self.list_probe_usernames():
            for entry in self._read_lines(self._sidecar(username, "profiles")):
                name, separator, profile = entry.partition("/")
                if separator and profile:
                    assignments.setdefault((name, profile), []).append(username)
        return assignments

    def read_iperf_profile_material(self, name: str) -> IperfProfileMaterial:
        """Everything the profile of one endpoint is made of.

        Only the profile-deployment path calls this; the values go straight to
        the probe helper over the encrypted channel.
        """
        import base64

        if not NAME_PATTERN.match(name):
            raise NotFoundError.of("iperf_endpoint", name)
        record = self._settings.iperf_dir / f"{name}.env"
        key_file = self._settings.iperf_dir / f"{name}.pem"
        if not record.is_file():
            raise NotFoundError.of("iperf_endpoint", name)
        values = read_env_file(record)
        password = values.get("IPERF_PASSWORD", "")
        if not password:
            raise RuntimeStateError(
                params={"endpoint": name},
                details="endpoint record has no password",
            )
        public_key_b64 = ""
        if key_file.is_file():
            public_key_b64 = base64.b64encode(key_file.read_bytes()).decode("ascii")
        return IperfProfileMaterial(
            password=password,
            public_key_b64=public_key_b64,
            host=values.get("IPERF_HOST", ""),
            port=_as_int(values.get("IPERF_PORT"), 5201),
            username=values.get("IPERF_USERNAME", ""),
        )

    # --- Helpers ------------------------------------------------------------

    @staticmethod
    def _read_lines(path: Path) -> tuple[str, ...]:
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError):
            return ()
        return tuple(
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.startswith("#")
        )


def _as_bool(value: str | None, *, default: bool) -> bool:
    """A missing key keeps the default; anything else has to say so plainly.

    The default matters: records the shell tooling wrote carry no such key, and
    that path only ever produced endpoints it had just set up itself.
    """
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"true", "yes", "1"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
