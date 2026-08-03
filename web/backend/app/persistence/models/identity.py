"""Web accounts, roles and sessions.

Sessions are stored server-side and referenced by an opaque cookie value. That
is one database round trip more than a signed token, and in exchange an
administrator can revoke a session immediately - which matters for a tool that
can restart the monitoring backbone.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base, IdMixin, TimestampMixin, UtcDateTime


class WebUser(Base, IdMixin, TimestampMixin):
    __tablename__ = "web_user"

    # Case-insensitive uniqueness is enforced by storing the folded form
    # alongside; SQLite's COLLATE NOCASE would not survive a PostgreSQL move.
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_folded: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_names(self) -> set[str]:
        return {assignment.role for assignment in self.roles}


class UserRole(Base, IdMixin):
    __tablename__ = "user_role"
    __table_args__ = (UniqueConstraint("user_id", "role"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("web_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped[WebUser] = relationship(back_populates="roles")


class Session(Base, IdMixin, TimestampMixin):
    __tablename__ = "session"
    __table_args__ = (Index("ix_session_expires_at", "expires_at"),)

    # Only the hash is stored: a database dump must not hand out live sessions.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("web_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    user: Mapped[WebUser] = relationship(back_populates="sessions", lazy="selectin")


class LoginAttempt(Base, IdMixin):
    """Throttling state keyed by username, so a wrong password cannot be used
    to enumerate accounts by timing."""

    __tablename__ = "login_attempt"

    username_folded: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, index=True
    )
