"""Sign-in, sessions and the first-run administrator.

Local accounts are the built-in way in. This platform is also the recovery path
for the NATS backbone, so it has to work when the identity provider is exactly
what is broken. OIDC is planned beside this, never instead of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AccountLockedError,
    ConflictError,
    InvalidCredentialsError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.core.permissions import Permission, RoleName, permissions_for_roles
from app.core.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    needs_rehash,
    password_problems,
    verify_password,
)
from app.persistence.models.identity import (
    LoginAttempt,
    Session,
    UserRole,
    WebUser,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is acting, resolved once per request."""

    user_id: str
    username: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[Permission]
    locale: str
    session_id: str | None = None
    is_development: bool = False

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    @classmethod
    def development(cls) -> Principal:
        """The clearly-labelled development identity.

        Guarded twice: the setting refuses to combine with a production
        environment, and the interface shows a permanent banner.
        """
        return cls(
            user_id="development",
            username="development",
            display_name="Development",
            roles=frozenset({RoleName.ADMINISTRATOR.value}),
            permissions=frozenset(Permission),
            locale="en",
            is_development=True,
        )


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._db = session
        self._settings = settings

    # --- First run ----------------------------------------------------------

    async def has_any_user(self) -> bool:
        count = await self._db.scalar(select(func.count()).select_from(WebUser))
        return bool(count)

    async def create_initial_administrator(
        self, username: str, password: str, display_name: str = ""
    ) -> WebUser:
        """Only possible while no account exists.

        The window closes the moment the first account is created, so this
        endpoint cannot be used to add a second administrator later.
        """
        if await self.has_any_user():
            raise ConflictError(
                params={"resource": "web_user"},
                details="an administrator already exists",
            )
        return await self.create_user(
            username=username,
            password=password,
            display_name=display_name,
            roles={RoleName.ADMINISTRATOR.value},
        )

    # --- Accounts -----------------------------------------------------------

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str = "",
        email: str | None = None,
        roles: set[str] | None = None,
        must_change_password: bool = False,
    ) -> WebUser:
        problems = password_problems(password)
        if problems:
            raise ValidationFailedError(
                fields=["password"],
                params={"problems": problems},
                details="password does not meet the minimum requirements",
            )
        folded = username.strip().casefold()
        if not folded:
            raise ValidationFailedError(fields=["username"])

        existing = await self._db.scalar(
            select(WebUser).where(WebUser.username_folded == folded)
        )
        if existing is not None:
            raise ConflictError(
                params={"resource": "web_user", "username": username},
                details="username already taken",
            )

        user = WebUser(
            username=username.strip(),
            username_folded=folded,
            display_name=display_name or username.strip(),
            email=email,
            password_hash=hash_password(password),
            must_change_password=must_change_password,
        )
        self._db.add(user)
        await self._db.flush()

        for role in roles or {RoleName.VIEWER.value}:
            self._db.add(UserRole(user_id=user.id, role=role))
        await self._db.flush()
        await self._db.refresh(user, ["roles"])
        return user

    async def change_password(
        self, user: WebUser, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise InvalidCredentialsError()
        problems = password_problems(new_password)
        if problems:
            raise ValidationFailedError(
                fields=["new_password"], params={"problems": problems}
            )
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # Every other session of this user dies with the old password.
        await self.revoke_all_sessions(user.id)

    # --- Sign-in ------------------------------------------------------------

    async def authenticate(
        self, username: str, password: str, *, source_ip: str | None = None
    ) -> WebUser:
        folded = username.strip().casefold()
        user = await self._db.scalar(
            select(WebUser).where(WebUser.username_folded == folded)
        )

        now = datetime.now(UTC)
        if user is not None and user.locked_until and user.locked_until > now:
            raise AccountLockedError(
                params={
                    "retry_after_seconds": int(
                        (user.locked_until - now).total_seconds()
                    )
                }
            )

        # Verify even when the account does not exist, against a throwaway
        # hash, so a missing username and a wrong password take the same time.
        password_ok = (
            verify_password(password, user.password_hash) if user is not None else False
        )
        if user is None:
            verify_password(password, _DUMMY_HASH)

        self._db.add(
            LoginAttempt(
                username_folded=folded,
                source_ip=source_ip,
                succeeded=password_ok,
                attempted_at=now,
            )
        )

        if user is None or not password_ok:
            if user is not None:
                await self._register_failure(user, now)
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InvalidCredentialsError(details="account is disabled")

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user

    async def _register_failure(self, user: WebUser, now: datetime) -> None:
        """Exponential back-off after the configured number of failures.

        Doubling with a ceiling: annoying for a human who mistyped, expensive
        for a script, and never a permanent lock-out that would need a second
        administrator to undo.
        """
        user.failed_login_count += 1
        if user.failed_login_count < self._settings.login_max_attempts:
            return
        over = user.failed_login_count - self._settings.login_max_attempts
        delay = min(
            self._settings.login_lockout_base_seconds * (2**over),
            self._settings.login_lockout_max_seconds,
        )
        user.locked_until = now + timedelta(seconds=delay)
        logger.warning(
            "account locked after repeated failures",
            extra={"username": user.username, "lock_seconds": delay},
        )

    # --- Sessions -----------------------------------------------------------

    async def issue_session(
        self,
        user: WebUser,
        *,
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        token = generate_session_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self._settings.session_lifetime_hours)
        self._db.add(
            Session(
                token_hash=hash_session_token(token),
                user_id=user.id,
                expires_at=expires_at,
                last_seen_at=now,
                source_ip=source_ip,
                user_agent=(user_agent or "")[:255] or None,
            )
        )
        await self._db.flush()
        return IssuedSession(token=token, expires_at=expires_at)

    async def resolve_session(self, token: str) -> tuple[WebUser, Session] | None:
        """Look up a live session and slide its idle window forward."""
        record = await self._db.scalar(
            select(Session).where(Session.token_hash == hash_session_token(token))
        )
        if record is None or record.revoked_at is not None:
            return None

        now = datetime.now(UTC)
        if record.expires_at <= now:
            return None

        idle_limit = timedelta(minutes=self._settings.session_idle_timeout_minutes)
        if now - record.last_seen_at > idle_limit:
            record.revoked_at = now
            return None

        record.last_seen_at = now
        user = record.user
        if not user.is_active:
            return None
        return user, record

    async def revoke_session(self, session_id: str) -> None:
        record = await self._db.get(Session, session_id)
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)

    async def revoke_all_sessions(self, user_id: str) -> None:
        now = datetime.now(UTC)
        sessions = await self._db.scalars(
            select(Session).where(
                Session.user_id == user_id, Session.revoked_at.is_(None)
            )
        )
        for record in sessions:
            record.revoked_at = now

    @staticmethod
    def principal_for(user: WebUser, session_id: str | None = None) -> Principal:
        roles = user.role_names
        return Principal(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name or user.username,
            roles=frozenset(roles),
            permissions=permissions_for_roles(roles),
            locale=user.locale,
            session_id=session_id,
        )


# A valid Argon2 hash of a value nobody knows, used only to spend the same time
# on a missing account as on a real one.
_DUMMY_HASH = hash_password("prtg-nats-timing-equaliser")
