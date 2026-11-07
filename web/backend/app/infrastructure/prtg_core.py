"""Reading probe names from the PRTG core through its API v2.

Read-only by design. PRTG is where probes are named, and this platform only
looks: every request here is a GET, and there is no code path that could
write.

The connection settings live in ``runtime/private/prtg/`` rather than in
``.env``, for the reason the overlay's do: the API container has no access to
the file beside the checkout, and an administrator should not need a shell to
set a URL (ADR 0002). The token sits there with mode 0600 like every other
secret in ``private/``, and nothing outside this module ever reads it.

Two facts about PRTG shaped this module, both checked against a real core:

- PRTG exposes no probe GID or GUID - not in API v2 (``/experimental/probes``,
  ``/probes/{id}``, its overview and schema) and not through the classic
  ``getobjectproperty.htm``. A probe cannot be found there by the identity
  this platform knows, which is why each probe is linked to its PRTG object
  id once, by a person, and looked up by that id.
- A core's certificate is often issued by an internal CA and names a host,
  not an address. Verification therefore takes an optional CA bundle and is
  never switched off - a token sent to an unverified peer is a token handed
  to whoever answers.
"""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from fastapi import status

from app.core.config import Settings
from app.core.errors import (
    PrtgNotConfiguredError,
    PrtgRejectedError,
    PrtgUnreachableError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.infrastructure.runtime_files import read_env_file

logger = get_logger(__name__)

# A PRTG object id is a positive integer. Spelled out because it ends up in a
# URL path: "../1" would otherwise be a request to somewhere else entirely.
OBJECT_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")
API_PREFIX = "/api/v2"
# PRTG's own page size without a limit is 3000. A core with more objects than
# one page holds is read page by page, so a probe beyond the first page is not
# missing from the pick list or reported as deleted in the probe list.
PROBE_PAGE_LIMIT = 3000
# A core that ignores the offset would hand back the first page forever.
MAX_PROBE_PAGES = 100
PROBE_KIND = "probenode"
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class PrtgCoreSettings:
    core_url: str | None
    # Excluded from repr so a stray log line of the settings cannot carry it.
    token: str | None = field(default=None, repr=False)
    ca_bundle_path: Path | None = None

    @property
    def usable(self) -> bool:
        return bool(self.core_url and self.token)


@dataclass(frozen=True, slots=True)
class PrtgProbe:
    object_id: str
    name: str
    status: str | None


def normalise_core_url(value: str) -> str:
    """``https://host[:port]``, with any ``/api/v2`` an operator pasted removed.

    HTTPS only: the token travels in every request header.
    """
    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme != "https" or not parts.hostname:
        raise ValidationFailedError(
            fields=["core_url"], details="the PRTG core URL must start with https://"
        )
    path = parts.path.rstrip("/")
    if path.endswith(API_PREFIX):
        path = path[: -len(API_PREFIX)]
    return f"https://{parts.netloc}{path}"


def validate_ca_bundle(value: str) -> str:
    try:
        certificates = x509.load_pem_x509_certificates(value.encode("utf-8"))
    except ValueError as exc:
        raise ValidationFailedError(
            fields=["ca_bundle"], details="the CA bundle is not a PEM certificate"
        ) from exc
    if not certificates:
        raise ValidationFailedError(
            fields=["ca_bundle"], details="the CA bundle holds no certificate"
        )
    return value.strip() + "\n"


def validate_object_id(value: str) -> str:
    candidate = value.strip()
    if not OBJECT_ID_PATTERN.match(candidate):
        raise ValidationFailedError(
            fields=["object_id"], details="a PRTG object id is a positive number"
        )
    return candidate


class PrtgCoreStore:
    """The connection settings, in the runtime's private area."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def directory(self) -> Path:
        return self._settings.private_dir / "prtg"

    @property
    def settings_path(self) -> Path:
        return self.directory / "settings"

    @property
    def token_path(self) -> Path:
        return self.directory / "api-token"

    @property
    def ca_bundle_path(self) -> Path:
        return self.directory / "ca-bundle.pem"

    def read(self) -> PrtgCoreSettings:
        values = read_env_file(self.settings_path)
        token = None
        if self.token_path.is_file():
            token = self.token_path.read_text(encoding="utf-8").strip() or None
        return PrtgCoreSettings(
            core_url=values.get("CORE_URL") or None,
            token=token,
            ca_bundle_path=(
                self.ca_bundle_path if self.ca_bundle_path.is_file() else None
            ),
        )

    def write(
        self,
        *,
        core_url: str | None,
        token: str | None = None,
        clear_token: bool = False,
        ca_bundle: str | None = None,
        clear_ca_bundle: bool = False,
    ) -> None:
        """Store what was given. A token or bundle left out stays as it was.

        The form never shows the stored token, so an empty field means "no
        change" - clearing it is a separate, explicit request.

        Everything is validated before the first file is touched. A refused
        CA bundle after a written URL would leave a new core paired with the
        old bundle, and the next read would send the token to it.
        """
        url = normalise_core_url(core_url) if core_url else ""
        bundle = (
            validate_ca_bundle(ca_bundle) if ca_bundle and not clear_ca_bundle else None
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        _write_private(
            self.settings_path,
            "# Written by prtg-nats. Change it through the interface.\n"
            f"CORE_URL={url}\n",
        )
        if clear_token:
            self.token_path.unlink(missing_ok=True)
        elif token:
            _write_private(self.token_path, token.strip() + "\n")
        if clear_ca_bundle:
            self.ca_bundle_path.unlink(missing_ok=True)
        elif bundle is not None:
            _write_private(self.ca_bundle_path, bundle)


def _write_private(path: Path, content: str) -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(content, encoding="utf-8")


class PrtgCoreClient:
    """The two reads this platform needs, and nothing else."""

    def __init__(
        self,
        settings: PrtgCoreSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not settings.core_url or not settings.token:
            raise PrtgNotConfiguredError()
        self._base_url = settings.core_url + API_PREFIX
        self._token = settings.token
        self._transport = transport
        # Built eagerly so a broken bundle fails here, not on the first read.
        self._verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=str(settings.ca_bundle_path))
            if settings.ca_bundle_path is not None
            else True
        )

    async def list_probes(self) -> list[PrtgProbe]:
        """Every probe on the core, read a page at a time."""
        probes: dict[str, PrtgProbe] = {}
        for page in range(MAX_PROBE_PAGES):
            answer = await self._get(
                "/experimental/probes",
                params={
                    "offset": str(page * PROBE_PAGE_LIMIT),
                    "limit": str(PROBE_PAGE_LIMIT),
                },
            )
            if not isinstance(answer, list):
                raise PrtgUnreachableError(details="the probe list is not a list")
            before = len(probes)
            for item in answer:
                probe = _probe_from(item)
                if probe is not None:
                    probes.setdefault(probe.object_id, probe)
            # A short page is the last one. A page with nothing new is a core
            # that ignores the offset; reading on would only repeat it.
            if len(answer) < PROBE_PAGE_LIMIT or len(probes) == before:
                break
        return list(probes.values())

    async def get_probe(self, object_id: str) -> PrtgProbe | None:
        """The probe with this object id, or None where PRTG has none.

        The one place an object id is checked: it ends up in a URL path, and
        every caller - a hand-entered link, a stored one - comes through here.
        """
        object_id = validate_object_id(object_id)
        answer = await self._get(f"/probes/{object_id}", missing_ok=True)
        if answer is None:
            return None
        # Only an object PRTG itself calls a probe. Whether /probes/{id}
        # answers for a group or a device is PRTG's business, not something
        # a link may rest on.
        if not isinstance(answer, dict) or answer.get("kind") != PROBE_KIND:
            return None
        probe = _probe_from(answer)
        if probe is None or probe.object_id != object_id:
            return None
        return probe

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                transport=self._transport,
                verify=self._verify,
                timeout=TIMEOUT_SECONDS,
                headers={"Authorization": f"Bearer {self._token}"},
                follow_redirects=False,
            ) as client:
                response = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            # The text of the exception names hosts, addresses and proxies of
            # the network behind this platform. It stays in the server log;
            # clients, jobs and the audit trail get the kind of failure.
            logger.warning(
                "PRTG core unreachable",
                extra={
                    "error": type(exc).__name__,
                    "reason": str(exc),
                    "path": path,
                },
            )
            raise PrtgUnreachableError(
                details=f"the PRTG core did not answer ({type(exc).__name__})"
            ) from exc

        if response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ):
            raise PrtgRejectedError(details=f"HTTP {response.status_code}")
        if missing_ok and response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        if response.status_code != status.HTTP_200_OK:
            raise PrtgUnreachableError(details=f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise PrtgUnreachableError(details="the answer is not JSON") from exc


def _probe_from(item: Any) -> PrtgProbe | None:
    if not isinstance(item, dict):
        return None
    object_id = item.get("id")
    name = item.get("name")
    if not isinstance(object_id, str | int) or not isinstance(name, str):
        return None
    # The list endpoint returns probes only; an item that says otherwise is
    # left out. get_probe() is stricter and requires the kind to be named.
    kind = item.get("kind")
    if kind is not None and kind != PROBE_KIND:
        return None
    raw_status = item.get("status")
    return PrtgProbe(
        object_id=str(object_id),
        name=name,
        status=raw_status if isinstance(raw_status, str) else None,
    )
