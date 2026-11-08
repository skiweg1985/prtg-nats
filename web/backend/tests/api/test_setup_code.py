"""Creating the first administrator needs the code printed on the host.

Without it, whoever reached the interface first on a fresh installation got
the administrator account. The code comes from `prtg-nats setup` or
`prtg-nats setup-code`, so only someone with a shell on the host can finish
the setup.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.core.config import Settings
from app.services.setup_code import SetupCodeStore

PASSWORD = "correct-horse-battery"


async def _setup(client: AsyncClient, code: str | None) -> int:
    body: dict[str, str] = {"username": "admin", "password": PASSWORD}
    if code is not None:
        body["setup_code"] = code
    response = await client.post("/api/v1/auth/setup", json=body)
    return response.status_code


async def _nobody_exists(client: AsyncClient) -> bool:
    state = (await client.get("/api/v1/auth/state")).json()
    return bool(state["setup_required"]) and not state["authenticated"]


async def test_setup_without_a_code_is_refused(
    client: AsyncClient, settings: Settings
) -> None:
    SetupCodeStore(settings).issue()

    assert await _setup(client, None) == 422
    assert await _nobody_exists(client)


async def test_setup_with_a_wrong_code_is_refused(
    client: AsyncClient, settings: Settings
) -> None:
    SetupCodeStore(settings).issue()

    response = await client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": PASSWORD, "setup_code": "AAAA-AAAA"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.setup_code_invalid"
    assert await _nobody_exists(client)


async def test_setup_is_refused_while_no_code_was_issued(
    client: AsyncClient, settings: Settings
) -> None:
    SetupCodeStore(settings).path.unlink(missing_ok=True)

    assert await _setup(client, "ANYTHING") == 403
    assert await _nobody_exists(client)


async def test_an_expired_code_is_refused(
    client: AsyncClient, settings: Settings
) -> None:
    store = SetupCodeStore(settings)
    code = store.issue()
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store.path.write_text(json.dumps(record), encoding="utf-8")

    assert await _setup(client, code) == 403
    assert await _nobody_exists(client)


async def test_a_new_code_replaces_the_previous_one(
    client: AsyncClient, settings: Settings
) -> None:
    store = SetupCodeStore(settings)
    old = store.issue()
    new = store.issue()

    assert await _setup(client, old) == 403
    assert await _setup(client, new) == 201


async def test_the_code_works_once_and_is_then_gone(
    client: AsyncClient, settings: Settings
) -> None:
    store = SetupCodeStore(settings)
    code = store.issue()

    assert await _setup(client, code) == 201
    assert not store.path.exists()


async def test_the_code_is_case_and_dash_insensitive(
    client: AsyncClient, settings: Settings
) -> None:
    """It is read off a terminal and typed by hand."""
    code = SetupCodeStore(settings).issue()

    assert await _setup(client, f"  {code.replace('-', '').lower()} ") == 201


def test_only_a_hash_is_stored(settings: Settings) -> None:
    store = SetupCodeStore(settings)
    code = store.issue()

    stored = store.path.read_text(encoding="utf-8")
    assert code not in stored
    assert code.replace("-", "") not in stored
    assert store.path.stat().st_mode & 0o077 == 0


async def test_a_refused_password_leaves_the_code_usable(
    client: AsyncClient, settings: Settings
) -> None:
    code = SetupCodeStore(settings).issue()

    weak = await client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": " padded-password ", "setup_code": code},
    )
    assert weak.status_code == 422, weak.text

    assert await _setup(client, code) == 201


def test_only_one_claim_wins(settings: Settings) -> None:
    """Two requests with the same code: the second finds it taken."""
    store = SetupCodeStore(settings)
    code = store.issue()

    assert store.claim(code)
    assert not store.claim(code)
    store.release()
    assert store.claim(code)
    store.consume()
    assert not store.claim(code)
