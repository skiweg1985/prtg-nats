"""Sign-in, sessions and the first-run wizard."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.persistence.models.audit import AuditEvent

PASSWORD = "correct-horse-battery"


async def test_a_fresh_installation_asks_for_setup(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/state")
    assert response.status_code == 200
    body = response.json()
    assert body["setup_required"] is True
    assert body["authenticated"] is False


async def test_setup_creates_an_administrator_and_signs_them_in(
    client: AsyncClient, settings: Settings
) -> None:
    response = await client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": PASSWORD, "display_name": "Admin"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["principal"]["roles"] == ["administrator"]
    assert "probe.read" in body["principal"]["permissions"]
    assert settings.session_cookie_name in response.cookies


async def test_setup_runs_only_once(client: AsyncClient) -> None:
    """The window closes with the first account, so this cannot add a second."""
    payload = {"username": "admin", "password": PASSWORD}
    assert (await client.post("/api/v1/auth/setup", json=payload)).status_code == 201

    second = await client.post(
        "/api/v1/auth/setup", json={"username": "other", "password": PASSWORD}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "common.conflict"


async def test_login_rejects_a_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    await client.post("/api/v1/auth/logout")

    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid_credentials"


async def test_an_unknown_user_gets_the_same_error_as_a_wrong_password(
    client: AsyncClient,
) -> None:
    """Otherwise the response enumerates which accounts exist."""
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )

    unknown = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": PASSWORD}
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]


async def test_repeated_failures_lock_the_account(
    client: AsyncClient, settings: Settings
) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )

    for _ in range(settings.login_max_attempts):
        await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )

    locked = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "auth.account_locked"
    assert locked.json()["error"]["retryable"] is True


async def test_a_password_never_reaches_the_audit_trail(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-attempt"}
    )

    async with session_factory() as db:
        events = list(await db.scalars(select(AuditEvent)))

    assert events, "sign-in attempts must be recorded"
    serialised = "".join(str(event.__dict__) for event in events)
    assert PASSWORD not in serialised
    assert "s3cret-attempt" not in serialised


async def test_a_failed_sign_in_is_recorded(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """It is the only trace an attempted intrusion leaves."""
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )

    async with session_factory() as db:
        events = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.action == "auth.login")
            )
        )
    assert any(event.result.value == "failure" for event in events)


async def test_logout_invalidates_the_session(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_an_unauthenticated_request_is_refused(client: AsyncClient) -> None:
    response = await client.get("/api/v1/probes")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.authentication_required"


async def test_a_short_password_is_refused_with_the_field_named(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": "short"}
    )
    assert response.status_code == 422
    assert "password" in response.json()["error"]["fields"]


async def test_audit_events_cannot_be_modified(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The database refuses it, not only the repository."""
    import pytest
    from sqlalchemy.exc import DatabaseError

    await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )

    async with session_factory() as db:
        event = await db.scalar(select(AuditEvent).limit(1))
        assert event is not None
        event.action = "tampered"
        with pytest.raises(DatabaseError):
            await db.commit()
