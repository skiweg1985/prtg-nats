"""The name a probe has in PRTG today, read through the PRTG API v2.

PRTG exposes no probe GID anywhere, so the platform cannot find a probe there
by the identity it knows. Instead each probe is linked once to its PRTG object
id - suggested by name, confirmed by a person - and the current name is read
by that id from then on. These tests pin down that bargain: the link lives in
the runtime inventory, nothing links silently, the token never leaves the
private area, and every way of not knowing the name says why.

PRTG itself is a recorded-shape fake behind an httpx transport; the item
fields are the ones a real core returns from ``/experimental/probes``.
"""

from __future__ import annotations

import datetime as dt
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.routes.prtg import get_prtg_transport
from app.core.config import Settings
from app.core.permissions import RoleName
from app.infrastructure.runtime_files import RuntimeFileStore
from tests.api.test_probe_access_key import sign_in
from tests.conftest import write_probe_inventory

TOKEN = "prtg-api-token-0f3c9a7e"
CORE_URL = "https://prtg.example.test:1616"
PROBE = "mpp-berlin-01"
OTHER = "mpp-hamburg-01"


def prtg_item(object_id: str, name: str, status: str = "CONNECTED") -> dict[str, Any]:
    """One probe the way ``GET /api/v2/experimental/probes`` lists it."""
    return {
        "basic": {"active": "1", "name": name, "priority": "3", "tags": []},
        "href": f"/api/v2/probes/{object_id}",
        "id": object_id,
        "kind": "probenode",
        "kind_name": "Probe",
        "name": name,
        "parent": {
            "id": "0",
            "name": "Root",
            "type": "REFERENCED_ROOT",
            "href": "/api/v2/groups/0",
        },
        "status": status,
        "type": "osk.probe",
    }


class FakePrtg:
    """A PRTG core that answers from a list of probes and records each call."""

    def __init__(self, probes: list[dict[str, Any]]) -> None:
        self.probes = probes
        self.requests: list[httpx.Request] = []
        self.fail: Callable[[httpx.Request], httpx.Response] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return self.fail(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "unauthorized"})
        path = request.url.path
        if path == "/api/v2/experimental/probes":
            return httpx.Response(200, json=self.probes)
        prefix = "/api/v2/probes/"
        if path.startswith(prefix):
            wanted = path.removeprefix(prefix)
            for item in self.probes:
                if item["id"] == wanted:
                    return httpx.Response(200, json=item)
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(404, json={"message": "no such path"})


@pytest.fixture
def prtg(app: Any) -> FakePrtg:
    fake = FakePrtg(
        [
            prtg_item("1", "Local Probe"),
            prtg_item("2041", "berlin-01"),
            prtg_item("2042", "Hamburg Office", status="DISCONNECTED"),
        ]
    )
    app.dependency_overrides[get_prtg_transport] = lambda: httpx.MockTransport(
        fake.handler
    )
    return fake


async def configure(client: AsyncClient, **extra: Any) -> httpx.Response:
    body: dict[str, Any] = {"core_url": CORE_URL, "token": TOKEN, **extra}
    response = await client.put("/api/v1/prtg/settings", json=body)
    assert response.status_code == 200, response.text
    return response


async def probe_ids(client: AsyncClient) -> dict[str, str]:
    listing = await client.get("/api/v1/probes")
    assert listing.status_code == 200, listing.text
    return {row["nats_username"]: row["id"] for row in listing.json()}


def self_signed_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example Internal CA")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


# --- Settings ---------------------------------------------------------------


async def test_the_token_is_kept_private_and_never_returned(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    await sign_in(client)

    saved = await configure(client)
    assert saved.json() == {
        "core_url": CORE_URL,
        "token_configured": True,
        "ca_bundle_configured": False,
    }
    read = await client.get("/api/v1/prtg/settings")
    assert TOKEN not in read.text

    stored = [
        path
        for path in settings.private_dir.rglob("*")
        if path.is_file() and TOKEN in path.read_text(encoding="utf-8")
    ]
    assert len(stored) == 1
    assert stat.S_IMODE(stored[0].stat().st_mode) == 0o600

    events = await client.get(
        "/api/v1/audit-events", params={"action": "prtg.settings.update"}
    )
    assert len(events.json()) == 1
    assert TOKEN not in events.text


async def test_saving_without_a_token_keeps_the_one_stored(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await sign_in(client)
    await configure(client)

    kept = await client.put(
        "/api/v1/prtg/settings", json={"core_url": "https://prtg2.example.test:1616"}
    )
    assert kept.json()["token_configured"] is True

    cleared = await client.put(
        "/api/v1/prtg/settings", json={"core_url": CORE_URL, "clear_token": True}
    )
    assert cleared.json()["token_configured"] is False


@pytest.mark.parametrize(
    "core_url", ["http://prtg.example.test:1616", "prtg.example.test", "ftp://x"]
)
async def test_only_an_https_core_url_is_accepted(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    core_url: str,
) -> None:
    """A token sent over plain HTTP is a token anyone on the path can use."""
    await sign_in(client)
    refused = await client.put(
        "/api/v1/prtg/settings", json={"core_url": core_url, "token": TOKEN}
    )
    assert refused.status_code == 422, refused.text


async def test_a_ca_bundle_must_be_a_certificate(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await sign_in(client)
    refused = await client.put(
        "/api/v1/prtg/settings",
        json={"core_url": CORE_URL, "token": TOKEN, "ca_bundle": "not a pem"},
    )
    assert refused.status_code == 422, refused.text

    accepted = await configure(client, ca_bundle=self_signed_pem())
    assert accepted.json()["ca_bundle_configured"] is True


async def test_an_operator_may_not_change_the_settings(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await sign_in(client, RoleName.OPERATOR)
    refused = await client.put(
        "/api/v1/prtg/settings", json={"core_url": CORE_URL, "token": TOKEN}
    )
    assert refused.status_code == 403, refused.text


# --- Linking ----------------------------------------------------------------


async def test_the_probe_whose_name_matches_is_suggested_not_linked(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]

    offered = await client.get(f"/api/v1/probes/{probe_id}/prtg-link/candidates")
    assert offered.status_code == 200, offered.text
    body = offered.json()
    assert body["suggested_object_id"] == "2041"
    assert body["candidates"][0] == {
        "object_id": "2041",
        "name": "berlin-01",
        "status": "CONNECTED",
    }
    assert {c["object_id"] for c in body["candidates"]} == {"1", "2041", "2042"}

    detail = await client.get(f"/api/v1/probes/{probe_id}")
    assert detail.json()["inventory"]["prtg_object_id"] is None


async def test_the_pick_list_leaves_out_probes_linked_elsewhere(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    write_probe_inventory(project_dir, OTHER, probe_name="hamburg-01")
    await sign_in(client)
    await configure(client)
    ids = await probe_ids(client)
    linked = await client.put(
        f"/api/v1/probes/{ids[OTHER]}/prtg-link", json={"object_id": "2042"}
    )
    assert linked.status_code == 200, linked.text

    offered = await client.get(f"/api/v1/probes/{ids[PROBE]}/prtg-link/candidates")
    assert {c["object_id"] for c in offered.json()["candidates"]} == {"1", "2041"}


async def test_no_suggestion_when_no_name_matches(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    """Renamed in PRTG before it was linked: the operator has to pick."""
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-old")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]

    offered = await client.get(f"/api/v1/probes/{probe_id}/prtg-link/candidates")
    assert offered.json()["suggested_object_id"] is None


async def test_a_confirmed_link_is_kept_in_the_runtime_inventory(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    settings: Settings,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]

    linked = await client.put(
        f"/api/v1/probes/{probe_id}/prtg-link", json={"object_id": "2041"}
    )
    assert linked.status_code == 200, linked.text

    inventory = (settings.probe_dir / f"{PROBE}.env").read_text(encoding="utf-8")
    assert "PRTG_OBJECT_ID=2041\n" in inventory
    detail = await client.get(f"/api/v1/probes/{probe_id}")
    assert detail.json()["inventory"]["prtg_object_id"] == "2041"

    removed = await client.delete(f"/api/v1/probes/{probe_id}/prtg-link")
    assert removed.status_code == 204, removed.text
    detail = await client.get(f"/api/v1/probes/{probe_id}")
    assert detail.json()["inventory"]["prtg_object_id"] is None


async def test_a_hand_entered_id_is_checked_against_prtg(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]

    unknown = await client.put(
        f"/api/v1/probes/{probe_id}/prtg-link", json={"object_id": "9999"}
    )
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["error"]["code"] == "prtg.object_not_found"

    garbage = await client.put(
        f"/api/v1/probes/{probe_id}/prtg-link", json={"object_id": "../1"}
    )
    assert garbage.status_code == 422, garbage.text


async def test_an_id_linked_to_another_probe_is_refused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    write_probe_inventory(project_dir, OTHER)
    await sign_in(client)
    await configure(client)
    ids = await probe_ids(client)
    await client.put(
        f"/api/v1/probes/{ids[OTHER]}/prtg-link", json={"object_id": "2041"}
    )

    refused = await client.put(
        f"/api/v1/probes/{ids[PROBE]}/prtg-link", json={"object_id": "2041"}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "prtg.object_id_taken"
    assert refused.json()["error"]["params"]["probe"] == OTHER


async def test_a_reconfiguration_keeps_the_link(settings: Settings) -> None:
    """write_probe_inventory() rewrites the file; the link must survive it."""
    runtime = RuntimeFileStore(settings)
    write_probe_inventory(settings.project_dir, PROBE)
    runtime.write_probe_prtg_object_id(PROBE, "2041")

    runtime.write_probe_inventory(
        nats_username=PROBE,
        ssh_host="192.0.2.30",
        probe_id="11111111-2222-3333-4444-555555555555",
        access_key="ACCESSKEY123",
        probe_name="Example Probe",
    )
    assert runtime.read_probe(PROBE).prtg_object_id == "2041"


# --- Reading the name -------------------------------------------------------


async def link(client: AsyncClient, probe_id: str, object_id: str) -> None:
    linked = await client.put(
        f"/api/v1/probes/{probe_id}/prtg-link", json={"object_id": object_id}
    )
    assert linked.status_code == 200, linked.text


async def test_the_current_name_is_read_by_object_id(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await link(client, probe_id, "2041")
    prtg.probes[1] = prtg_item("2041", "Berlin Office")
    prtg.requests.clear()

    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "state": "ok",
        "object_id": "2041",
        "enrollment_name": "berlin-01",
        "name_in_prtg": "Berlin Office",
        "differs": True,
    }
    (request,) = prtg.requests
    assert str(request.url) == f"{CORE_URL}/api/v2/probes/2041"


async def test_the_same_name_is_not_a_difference(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await link(client, probe_id, "2041")

    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.json()["differs"] is False


async def test_without_a_token_the_page_says_so(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    probe_id = (await probe_ids(client))[PROBE]

    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.json()["state"] == "not_configured"
    assert answer.json()["name_in_prtg"] is None
    assert prtg.requests == []


async def test_without_a_link_the_page_says_so(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]

    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.json()["state"] == "not_linked"
    assert prtg.requests == []


async def test_an_unreachable_core_is_reported_not_raised(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await link(client, probe_id, "2041")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    prtg.fail = refuse
    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.status_code == 200, answer.text
    assert answer.json()["state"] == "unreachable"
    assert answer.json()["name_in_prtg"] is None


async def test_a_refused_token_is_its_own_reason(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await link(client, probe_id, "2041")

    prtg.fail = lambda _: httpx.Response(401, json={"message": "unauthorized"})
    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.json()["state"] == "rejected"
    assert TOKEN not in answer.text


async def test_a_link_to_a_deleted_probe_says_so(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await link(client, probe_id, "2041")
    del prtg.probes[1]

    answer = await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    assert answer.json()["state"] == "not_found"


async def test_the_list_gets_every_linked_name_in_one_call(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    write_probe_inventory(project_dir, OTHER, probe_name="hamburg-01")
    await sign_in(client)
    await configure(client)
    ids = await probe_ids(client)
    await link(client, ids[PROBE], "2041")
    await link(client, ids[OTHER], "2042")
    prtg.requests.clear()

    answer = await client.get("/api/v1/prtg/probe-names")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["state"] == "ok"
    assert sorted(body["names"], key=lambda n: n["nats_username"]) == [
        {
            "nats_username": PROBE,
            "object_id": "2041",
            "name_in_prtg": "berlin-01",
            "differs": False,
        },
        {
            "nats_username": OTHER,
            "object_id": "2042",
            "name_in_prtg": "Hamburg Office",
            "differs": True,
        },
    ]
    assert len(prtg.requests) == 1


async def test_nothing_is_ever_written_to_prtg(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
    prtg: FakePrtg,
) -> None:
    write_probe_inventory(project_dir, PROBE, probe_name="berlin-01")
    await sign_in(client)
    await configure(client)
    probe_id = (await probe_ids(client))[PROBE]
    await client.get(f"/api/v1/probes/{probe_id}/prtg-link/candidates")
    await link(client, probe_id, "2041")
    await client.get(f"/api/v1/probes/{probe_id}/prtg-name")
    await client.get("/api/v1/prtg/probe-names")

    assert prtg.requests
    assert {request.method for request in prtg.requests} == {"GET"}
    assert all(TOKEN not in json.dumps(dict(r.url.params)) for r in prtg.requests)
