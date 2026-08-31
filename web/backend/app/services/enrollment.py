"""Enrolling a host by invitation.

The shell tooling opened an SSH session to the target as its administrator and
worked from there. That needs a stranger's root password to travel through
this process, and it needs the platform to be able to reach the host at the
moment of enrolment.

This turns the direction around. The platform mints a single-use token and
prints a command; an operator runs that command on the host; the host installs
the restricted management access itself and reports back. What crosses the
wire is a secret this platform issued and can revoke - not a standing
administrator credential - and the host proves it is the intended one by being
where the operator typed the command.

After that, nothing changes: the management channel is the same restricted
SSH forced command it always was, and the same libexec/enroll-probe.sh
installs it. That script is served, not reimplemented.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import EnrollmentTokenInvalidError, RuntimeStateError
from app.core.security import hash_session_token
from app.infrastructure.certificates import fingerprint_of_pem
from app.infrastructure.helper_signing import HelperSigner
from app.infrastructure.overlay import OverlayRuntime
from app.infrastructure.runtime_files import RuntimeFileStore, SiteSettings
from app.persistence.models.inventory import EnrollmentToken
from app.services.auth import Principal

# Long enough that guessing is not a strategy, short enough to retype from a
# screen if copy and paste is not available.
TOKEN_BYTES = 32

# The window an operator needs to walk to a console and paste a command. Long
# enough to be practical, short enough that a forgotten invitation expires on
# its own rather than waiting to be found.
DEFAULT_TTL_MINUTES = 60

PROBE = "probe"
IPERF = "iperf"

# What the bootstrap script is allowed to fetch. A closed set, because the
# request that fetches these carries a valid token but no identity beyond it.
PROBE_ASSETS: dict[str, str] = {
    "enroll-probe.sh": "libexec/enroll-probe.sh",
    "prtg-nats-probe-helper": "libexec/prtg-nats-probe-helper",
    "install-mpp.sh": "install-mpp.sh",
}
_IPERF_ENDPOINT_DIR = "sensors/iperf-throughput/endpoint"
IPERF_ASSETS: dict[str, str] = {
    "iperf-enroll.sh": "libexec/iperf-enroll.sh",
    "prtg-nats-iperf-helper": "libexec/prtg-nats-iperf-helper",
    # Installed next to the helper, which calls it rather than reimplementing
    # what it does. It is also the manual path the sensor's README documents,
    # so it stays where the sensor keeps it.
    "setup-iperf3-endpoint.sh": f"{_IPERF_ENDPOINT_DIR}/setup-iperf3-endpoint.sh",
}


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """The cleartext token, which exists only in this return value."""

    token: str
    record: EnrollmentToken


@dataclass(frozen=True, slots=True)
class EnrolmentTarget:
    """What the caller asked to create, resolved against the site settings."""

    kind: str
    payload: dict[str, Any]
    expected_host: str | None
    ttl_minutes: int


class EnrollmentService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._runtime = RuntimeFileStore(settings)

    # --- Issuing ------------------------------------------------------------

    async def issue(
        self, target: EnrolmentTarget, principal: Principal | None
    ) -> IssuedToken:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        payload = dict(target.payload)
        if target.kind == PROBE:
            payload.update(await self._reserve_overlay_address())
        record = EnrollmentToken(
            kind=target.kind,
            token_hash=hash_session_token(token),
            payload=payload,
            expected_host=target.expected_host,
            expires_at=datetime.now(UTC) + timedelta(minutes=target.ttl_minutes),
            created_by_id=getattr(principal, "user_id", None),
            created_by_name=getattr(principal, "username", None),
        )
        self._db.add(record)
        await self._db.flush()
        return IssuedToken(token=token, record=record)

    async def _reserve_overlay_address(self) -> dict[str, str]:
        """Promise this invitation an address, if there is an overlay at all.

        Reserved when the invitation is issued rather than when it is redeemed,
        because that is the only moment the platform is certain to be talking
        to somebody: a probe behind NAT reports in once and is never reachable
        the other way, so its tunnel has to be configurable from the script it
        was handed.
        """
        site = self._runtime.site_settings()
        if not site.overlay_enabled or not site.overlay_endpoint:
            return {}
        overlay = OverlayRuntime(self._settings)
        overlay.check_endpoint_collision()
        reserved = [
            record.payload["overlay_address"]
            for record in await self.list_open(PROBE)
            if record.payload.get("overlay_address")
        ]
        return {
            "overlay_address": overlay.allocate_address(reserved),
            "overlay_mode": site.overlay_default_mode,
        }

    async def list_open(self, kind: str) -> list[EnrollmentToken]:
        """Invitations that could still be used, newest first."""
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(EnrollmentToken)
            .where(
                EnrollmentToken.kind == kind,
                EnrollmentToken.redeemed_at.is_(None),
                EnrollmentToken.revoked_at.is_(None),
                EnrollmentToken.expires_at > now,
            )
            .order_by(EnrollmentToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, token_id: str, *, kind: str | None = None) -> EnrollmentToken:
        """One invitation by id, in whatever state it is in.

        Deliberately unfiltered, unlike list_open(). Redeeming an invitation
        and writing the job id it started happen in the same request, so an
        invitation leaves the open list at the very moment it gains the job
        id - anything watching only that list loses the record exactly when
        the interesting part appears.
        """
        record = await self._db.get(EnrollmentToken, token_id)
        if record is None:
            raise EnrollmentTokenInvalidError()
        if kind is not None and record.kind != kind:
            raise EnrollmentTokenInvalidError()
        return record

    async def revoke(self, token_id: str) -> EnrollmentToken:
        record = await self.get(token_id)
        if record.revoked_at is None and record.redeemed_at is None:
            record.revoked_at = datetime.now(UTC)
        return record

    # --- Redeeming ----------------------------------------------------------

    async def resolve(self, token: str, *, kind: str | None = None) -> EnrollmentToken:
        """Find a usable token, or refuse without saying which rule it broke.

        Looked up by hash, so a database dump does not hand out live
        invitations, and the lookup is a plain equality on a unique column -
        no comparison of the caller's secret against a stored one.
        """
        result = await self._db.execute(
            select(EnrollmentToken).where(
                EnrollmentToken.token_hash == hash_session_token(token)
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise EnrollmentTokenInvalidError()
        if kind is not None and record.kind != kind:
            raise EnrollmentTokenInvalidError()
        if record.revoked_at is not None or record.redeemed_at is not None:
            raise EnrollmentTokenInvalidError()
        if record.expires_at <= datetime.now(UTC):
            raise EnrollmentTokenInvalidError()
        return record

    async def redeem(
        self,
        record: EnrollmentToken,
        *,
        source_ip: str | None,
        reported: dict[str, Any],
    ) -> None:
        """Mark the invitation used. Single use is the whole point."""
        record.redeemed_at = datetime.now(UTC)
        record.source_ip = source_ip
        record.reported = reported

    # --- What the host fetches ----------------------------------------------

    def asset_path(self, kind: str, name: str) -> Path:
        """Resolve a named asset, never a caller-supplied path.

        The lookup is a dictionary rather than a join against asset_dir: the
        request carries a valid token, but a token is not a reason to let the
        caller name a file.
        """
        table = PROBE_ASSETS if kind == PROBE else IPERF_ASSETS
        relative = table.get(name)
        if relative is None:
            raise EnrollmentTokenInvalidError()
        path = self._settings.asset_dir / relative
        if not path.is_file():
            raise RuntimeStateError(
                params={"path": str(path)}, details=f"asset is missing: {name}"
            )
        return path

    # --- The rendered script ------------------------------------------------

    def base_url(self) -> str:
        """Where the host reaches this platform, over TLS.

        The name, not the address: it is the name in the certificate the probe
        just learned to trust. The port is left out when it is the default
        one - this URL is pasted into a one-liner and read back from runbooks.
        """
        site = self._runtime.site_settings()
        if not site.web_fqdn:
            raise RuntimeStateError(details="NATS_FQDN is not configured")
        port = self._settings.web_https_port
        if port == 443:
            return f"https://{site.web_fqdn}"
        return f"https://{site.web_fqdn}:{port}"

    def ca_material(self) -> tuple[str, str]:
        """The CA and its fingerprint, as the probe will see them."""
        ca_path = self._settings.cert_dir / "ca.pem"
        if not ca_path.is_file():
            raise RuntimeStateError(
                params={"path": str(ca_path)},
                details="CA is missing; initialise the runtime first",
            )
        pem = ca_path.read_text(encoding="utf-8").strip()
        digest = hashlib.sha256(ca_path.read_bytes()).hexdigest()
        return pem, digest

    def render_bootstrap(self, record: EnrollmentToken, token: str) -> str:
        """Fill the template for this one invitation.

        The CA, the management public key and the helper signing key are
        embedded rather than fetched. All three are public, all three are
        already implied by the channel this script arrived over, and inlining
        them means one less thing to fail halfway through on a host with an
        awkward network.
        """
        template = (
            self._settings.asset_dir / "bootstrap" / "probe-bootstrap.sh.template"
        )
        if not template.is_file():
            raise RuntimeStateError(
                params={"path": str(template)}, details="bootstrap template is missing"
            )

        site = self._runtime.site_settings()
        source_cidr = site.ssh_source_cidr
        if not source_cidr:
            raise RuntimeStateError(
                details="MPP_SSH_SOURCE_CIDR is not configured and NATS_HOST_IP "
                "is unset; the management key would be valid from anywhere"
            )
        # The installer has no default for this and asks at a prompt the
        # bootstrap cannot answer - it runs from a pipe. Refused here, where an
        # operator is looking at the answer, rather than on a console halfway
        # through an installation.
        if not site.nats_fqdn:
            raise RuntimeStateError(
                details="NATS_FQDN is not configured; the probe would have "
                "nothing to install the package against"
            )

        ca_pem, ca_sha256 = self.ca_material()
        # Two digests of one certificate, and they are not interchangeable.
        # ca_sha256 is the hash of the file, which is what "sha256sum -c"
        # compares in the one-liner and in the bootstrap. install-mpp.sh means
        # the fingerprint of the certificate itself - the DER encoding - which
        # is also what the probe helper reports and what reconciliation
        # compares against. Handing the first where the second is expected is
        # a mismatch on every certificate there is.
        ca_fingerprint = fingerprint_of_pem(ca_pem)
        if not ca_fingerprint:
            raise RuntimeStateError(
                details="the CA in runtime/ is not a PEM certificate"
            )
        values = {
            "@@BASE_URL@@": self.base_url(),
            "@@TOKEN@@": token,
            "@@CA_PEM@@": ca_pem,
            "@@CA_SHA256@@": ca_sha256,
            "@@CA_FINGERPRINT@@": ca_fingerprint,
            "@@SSH_SOURCE_CIDR@@": self._source_cidr_with_hub(source_cidr, site),
            "@@NATS_HOST@@": site.nats_fqdn,
            "@@NATS_PORT@@": str(site.nats_port),
            "@@MANAGEMENT_PUBLIC_KEY@@": self.management_public_key(),
            "@@HELPER_SIGNING_KEY@@": HelperSigner(self._settings)
            .public_key_pem()
            .strip(),
            "@@INSTALL_PACKAGE@@": (
                "true" if record.payload.get("install_package", True) else "false"
            ),
        }
        values.update(self._overlay_values(record, site))

        return self._fill(template, values)

    def _overlay_values(
        self, record: EnrollmentToken, site: SiteSettings
    ) -> dict[str, str]:
        """What the bootstrap needs to bring the tunnel up by itself.

        Everything except the probe's own key, which the probe generates and
        reports back - the private half has no reason to exist here.
        """
        address = record.payload.get("overlay_address")
        endpoint = site.overlay_endpoint
        if not site.overlay_enabled or not address or not endpoint:
            return {
                "@@OVERLAY_ENABLED@@": "false",
                "@@OVERLAY_MODE@@": "off",
                "@@OVERLAY_ADDRESS@@": "",
                "@@OVERLAY_SUBNET@@": "",
                "@@OVERLAY_ENDPOINT@@": "",
                "@@OVERLAY_HUB_KEY@@": "",
                "@@OVERLAY_NATS_HOST_IP@@": "",
            }
        overlay = OverlayRuntime(self._settings)
        return {
            "@@OVERLAY_ENABLED@@": "true",
            "@@OVERLAY_MODE@@": str(
                record.payload.get("overlay_mode") or site.overlay_default_mode
            ),
            "@@OVERLAY_ADDRESS@@": str(address),
            "@@OVERLAY_SUBNET@@": site.overlay_subnet,
            "@@OVERLAY_ENDPOINT@@": endpoint,
            "@@OVERLAY_HUB_KEY@@": overlay.ensure_hub_key(),
            "@@OVERLAY_NATS_HOST_IP@@": site.nats_host_ip or "",
        }

    @staticmethod
    def _source_cidr_with_hub(source_cidr: str, site: SiteSettings) -> str:
        """The from= list the probe's management key is written with.

        The hub address is added, never substituted: a probe whose tunnel
        breaks has to stay reachable the way it would have been without one.
        """
        if not site.overlay_enabled:
            return source_cidr
        hub = f"{site.overlay_hub_address}/32"
        sources = [part.strip() for part in source_cidr.split(",") if part.strip()]
        if hub not in sources:
            sources.append(hub)
        return ",".join(sources)

    def render_iperf_bootstrap(self, record: EnrollmentToken, token: str) -> str:
        """The same for an iperf measurement endpoint, and shorter.

        Nothing secret is filled in here. The endpoint's password is generated
        on this side and travels over the management channel this script
        installs, not through the script itself: fetching it does not spend the
        invitation, so anything embedded would stay readable for as long as the
        token lives.
        """
        template = (
            self._settings.asset_dir / "bootstrap" / "iperf-bootstrap.sh.template"
        )
        if not template.is_file():
            raise RuntimeStateError(
                params={"path": str(template)},
                details="iperf bootstrap template is missing",
            )

        ca_pem, ca_sha256 = self.ca_material()
        return self._fill(
            template,
            {
                "@@BASE_URL@@": self.base_url(),
                "@@TOKEN@@": token,
                "@@CA_PEM@@": ca_pem,
                "@@CA_SHA256@@": ca_sha256,
                "@@SSH_SOURCE_CIDR@@": self.iperf_source_cidr(record),
                "@@MANAGEMENT_PUBLIC_KEY@@": self.management_public_key(),
            },
        )

    def iperf_source_cidr(self, record: EnrollmentToken) -> str:
        """Which network the endpoint will accept this platform from.

        The invitation's own answer wins, and there is deliberately no fallback
        to NATS_HOST_IP the way the probe path has one. A probe sees us under
        our internal address; an endpoint on a public network sees us under the
        address we leave the site with, and nothing here can derive that. A
        guess would install a management key valid from the wrong network, and
        the only repair is a walk to that host's console.
        """
        cidr = str(record.payload.get("ssh_source_cidr") or "").strip()
        if not cidr:
            cidr = (self._runtime.site_settings().iperf_ssh_source_cidr or "").strip()
        if not cidr:
            raise RuntimeStateError(
                details="no source network for the endpoint: set one on the "
                "invitation, or IPERF_SSH_SOURCE_CIDR for the site"
            )
        return cidr

    def _fill(self, template: Path, values: dict[str, str]) -> str:
        script = template.read_text(encoding="utf-8")
        for placeholder, value in values.items():
            script = script.replace(placeholder, value)

        remaining = re.findall(r"@@[A-Z_]+@@", script)
        if remaining:
            raise RuntimeStateError(
                params={"placeholders": ", ".join(sorted(set(remaining)))},
                details="bootstrap template has placeholders nothing filled in",
            )
        return script

    def one_liner(self, token: str, *, script: str = "bootstrap.sh") -> str:
        """The command an operator pastes on the host.

        Fetches the CA over plain HTTP first, checks it against a fingerprint
        that came through the browser, and only then speaks TLS. That is the
        same ceremony install-mpp.sh has always used - no --insecure anywhere.

        The script name is the only difference between a probe and an iperf
        endpoint here: both ceremonies are the same, and having one of them
        drift would be a second thing to keep right for no reason.
        """
        site = self._runtime.site_settings()
        _, ca_sha256 = self.ca_material()
        host = site.web_fqdn
        ca_port = "" if site.ca_http_port == 80 else f":{site.ca_http_port}"
        ca_url = f"http://{host}{ca_port}/nats-ca.pem"
        return (
            f"curl -fsSL {ca_url} -o /tmp/prtg-nats-ca.pem \\\n"
            f'  && echo "{ca_sha256}  /tmp/prtg-nats-ca.pem" | sha256sum -c - \\\n'
            f"  && curl -fsSL --cacert /tmp/prtg-nats-ca.pem \\\n"
            f"       {self.base_url()}/enroll/{token}/{script} | sudo sh"
        )

    def management_public_key(self) -> str:
        # Same spelling as pki.py: the key has no extension, so with_suffix()
        # would be wrong the moment someone renames it to contain a dot.
        path = Path(f"{self._settings.ssh_key_path}.pub")
        if not path.is_file():
            raise RuntimeStateError(
                params={"path": str(path)},
                details="management key is missing; initialise the runtime first",
            )
        return path.read_text(encoding="utf-8").strip()
