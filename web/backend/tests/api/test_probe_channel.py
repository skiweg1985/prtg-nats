"""The probe channel as the proxy in front of it hands requests over (ADR 0013).

Caddy verifies the client certificate against the probe CA and passes its
subject on in X-Probe-Identity, the way Go prints a distinguished name:
``CN=<identity>``. These tests send exactly that and go through the real
get_probe_identity - no override - so the mapping from certificate to probe is
what is under test.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography import x509
from httpx import AsyncClient

from app.core.config import Settings
from app.infrastructure.pki import Pki
from tests.conftest import probe_csr, write_probe_inventory

PROBE = "mpp-berlin-01"


def subject(identity: str) -> dict[str, str]:
    """The header as Caddy sets it from {http.request.tls.client.subject}."""
    return {"X-Probe-Identity": f"CN={identity}"}


# --- Identity mapping --------------------------------------------------------


async def test_the_certificate_subject_names_the_probe(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    csr, _ = probe_csr()
    response = await client.post(
        "/probe/v1/certificate", json={"csr": csr}, headers=subject(PROBE)
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty, as Caddy sends it without a certificate"),
        pytest.param(PROBE, id="a bare name, which the proxy never sends"),
        pytest.param(f"CN={PROBE},O=Example", id="more than the CN"),
        pytest.param(f"O=Example,CN={PROBE}", id="CN not alone"),
        pytest.param("CN=", id="an empty CN"),
        pytest.param("CN=mpp-unknown-09", id="not enrolled"),
        pytest.param("CN=../probes/mpp-berlin-01", id="a path"),
    ],
)
async def test_anything_but_the_subject_of_an_enrolled_probe_is_refused(
    client: AsyncClient, project_dir: Path, value: str
) -> None:
    write_probe_inventory(project_dir, PROBE)
    csr, _ = probe_csr()
    response = await client.post(
        "/probe/v1/certificate",
        json={"csr": csr},
        headers={"X-Probe-Identity": value},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.probe_not_enrolled"


def _caddy_sites() -> dict[str, str]:
    """The Caddyfile's top-level blocks, keyed by their address line.

    The global options block is the one under the empty key.
    """
    text = (Path(__file__).resolve().parents[3] / "Caddyfile").read_text(
        encoding="utf-8"
    )
    sites: dict[str, str] = {}
    address: str | None = None
    body: list[str] = []
    depth = 0
    for line in text.splitlines():
        # Comments here are whole lines; braces in their prose must not count.
        stripped = "" if line.lstrip().startswith("#") else line.strip()
        if depth == 0 and stripped.endswith("{"):
            address, body = stripped[:-1].strip(), []
        elif depth > 0:
            body.append(stripped)
        depth += stripped.count("{") - stripped.count("}")
        if depth == 0 and address is not None:
            sites[address] = "\n".join(body)
            address = None
    return sites


def test_the_proxy_sends_the_identity_in_the_form_read_here() -> None:
    """The Caddyfile and get_probe_identity live in different places.

    Neither knows about the other, so this holds them together: the probe
    channel sets the header from the verified subject - "CN=<identity>", as
    the tests above send it - and the interface site never forwards it.

    default_sni is part of it: a probe that connects by address sends no
    server name, and without it that handshake picked a policy with no client
    authentication at all - found with a real Caddy, not by reading the docs.
    """
    sites = _caddy_sites()
    (channel,) = [body for site, body in sites.items() if "PROBE_CHANNEL_PORT" in site]
    (interface,) = [body for site, body in sites.items() if "WEB_HTTPS_PORT" in site]

    assert re.search(r"default_sni\s+\{\$WEB_BIND_IP\}", sites[""])
    assert re.search(r"mode\s+require_and_verify", channel)
    assert "trust_pool file /etc/caddy/certs/probe-ca.pem" in channel
    assert re.search(
        r"header_up\s+X-Probe-Identity\s+\{http\.request\.tls\.client\.subject\}",
        channel,
    )
    # Only the probe routes are forwarded from the channel.
    assert re.findall(r"handle\s+(\S+)\s*\{", channel) == ["/probe/v1/*"]

    assert re.search(r"request_header\s+-X-Probe-Identity", interface)
    assert re.search(r"handle\s+/probe/\*\s*\{\s*respond\s+404", interface)


# --- Certificate renewal -----------------------------------------------------


async def test_renewal_returns_a_certificate_for_the_calling_probe(
    client: AsyncClient, project_dir: Path, settings: Settings
) -> None:
    write_probe_inventory(project_dir, PROBE)
    # The subject the probe wrote into its CSR does not matter; who called does.
    csr, key = probe_csr("mpp-somebody-else")

    response = await client.post(
        "/probe/v1/certificate", json={"csr": csr}, headers=subject(PROBE)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    certificate = x509.load_pem_x509_certificate(body["certificate"].encode())
    assert certificate.public_key() == key.public_key()
    assert certificate.subject.rfc4514_string() == f"CN={PROBE}"
    ca = x509.load_pem_x509_certificate(Pki(settings).probe_ca_pem().encode())
    assert certificate.issuer == ca.subject
    assert datetime.fromisoformat(body["not_after"]) == (
        certificate.not_valid_after_utc.replace(microsecond=0)
    )
    assert datetime.fromisoformat(body["not_after"]) > datetime.now(UTC)


async def test_an_unreadable_csr_is_refused(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    response = await client.post(
        "/probe/v1/certificate",
        json={"csr": "-----BEGIN CERTIFICATE REQUEST-----\nbm9wZQ==\n"},
        headers=subject(PROBE),
    )
    assert response.status_code == 422, response.text
