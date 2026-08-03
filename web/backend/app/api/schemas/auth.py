"""Authentication payloads."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.api.schemas.common import ApiModel


class LoginIn(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class SetupIn(ApiModel):
    """Creating the first administrator. Possible exactly once."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=512)
    display_name: str = Field(default="", max_length=128)


class ChangePasswordIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class PrincipalOut(ApiModel):
    user_id: str
    username: str
    display_name: str
    roles: list[str]
    # The full set, so the interface can gate controls without a second call.
    permissions: list[str]
    locale: str
    is_development: bool = False
    must_change_password: bool = False


class AuthStateOut(ApiModel):
    """Answers "can I sign in, and am I signed in?" in one call."""

    authenticated: bool
    setup_required: bool
    dev_auth: bool
    principal: PrincipalOut | None = None


class UserOut(ApiModel):
    id: str
    username: str
    display_name: str
    email: str | None
    roles: list[str]
    is_active: bool
    locale: str
    last_login_at: datetime | None
    locked_until: datetime | None
    created_at: datetime


class UserCreateIn(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=512)
    display_name: str = Field(default="", max_length=128)
    email: str | None = Field(default=None, max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    must_change_password: bool = True


class UserUpdateIn(ApiModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    roles: list[str] | None = None
    is_active: bool | None = None
    locale: str | None = Field(default=None, max_length=8)
