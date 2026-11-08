"""An account that has to change its password can do nothing else first.

The flag used to be reported once, in the sign-in answer, and then forgotten:
the session behaved like any other. The server enforces it now, so it holds
for every client, not only for the interface that happens to show a dialog.
"""

from __future__ import annotations

from httpx import AsyncClient

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "a-much-better-passphrase"


async def _operator_with_initial_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    created = await client.post(
        "/api/v1/users",
        json={
            "username": "operator",
            "password": PASSWORD,
            "roles": ["operator"],
            "must_change_password": True,
        },
    )
    assert created.status_code == 201, created.text
    await client.post("/api/v1/auth/logout")


async def _sign_in(client: AsyncClient, password: str = PASSWORD) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": password}
    )
    assert response.status_code == 200, response.text
    principal: dict[str, object] = response.json()["principal"]
    return principal


async def test_the_state_keeps_reporting_the_flag(client: AsyncClient) -> None:
    await _operator_with_initial_password(client)
    principal = await _sign_in(client)
    assert principal["must_change_password"] is True

    state = await client.get("/api/v1/auth/state")
    assert state.json()["principal"]["must_change_password"] is True


async def test_other_routes_refuse_until_the_password_is_changed(
    client: AsyncClient,
) -> None:
    await _operator_with_initial_password(client)
    await _sign_in(client)

    for path in ("/api/v1/jobs", "/api/v1/probes", "/api/v1/deployments"):
        response = await client.get(path)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "auth.password_change_required"

    retried = await client.post("/api/v1/jobs/anything/retry")
    assert retried.status_code == 403


async def test_signing_out_still_works(client: AsyncClient) -> None:
    await _operator_with_initial_password(client)
    await _sign_in(client)

    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    state = await client.get("/api/v1/auth/state")
    assert state.json()["authenticated"] is False


async def test_changing_the_password_lifts_the_restriction(
    client: AsyncClient,
) -> None:
    await _operator_with_initial_password(client)
    await _sign_in(client)

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204, changed.text

    # Still signed in, on a fresh session: the old one ended with the password.
    state = await client.get("/api/v1/auth/state")
    assert state.json()["authenticated"] is True
    assert state.json()["principal"]["must_change_password"] is False
    assert (await client.get("/api/v1/jobs")).status_code == 200


async def test_an_account_without_the_flag_is_not_affected(
    client: AsyncClient,
) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert (await client.get("/api/v1/jobs")).status_code == 200
    state = await client.get("/api/v1/auth/state")
    assert state.json()["principal"]["must_change_password"] is False


async def test_guessing_the_current_password_is_throttled_like_a_sign_in(
    client: AsyncClient,
) -> None:
    """A session cookie alone must not buy unlimited guesses at the password."""
    await _operator_with_initial_password(client)
    await _sign_in(client)

    for _ in range(5):
        wrong = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "not-the-password", "new_password": NEW_PASSWORD},
        )
        assert wrong.status_code == 401, wrong.text

    locked = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert locked.status_code == 429, locked.text
    assert locked.json()["error"]["code"] == "auth.account_locked"
