"""Declarative base and the columns every table shares."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, MetaData, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import new_id

# Explicit naming so Alembic autogenerate produces stable names and a later
# move to PostgreSQL does not rename every constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """Always store and return timezone-aware UTC.

    SQLite drops the offset, which silently turns an aware datetime into a
    naive one on the way back and breaks every comparison downstream.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime reached the database layer")
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class EnumString(TypeDecorator[Any]):
    """Store a StrEnum as text and read it back as the enum.

    A plain String column would round-trip to `str`, and every
    `status.is_terminal` or `status is JobStatus.RUNNING` on a loaded row would
    then be wrong - silently, because a StrEnum compares equal to its value.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_type: type[StrEnum], length: int = 32) -> None:
        self._enum_type = enum_type
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return str(self._enum_type(value))

    def process_result_value(self, value: Any, dialect: Any) -> StrEnum | None:
        if value is None:
            return None
        try:
            return self._enum_type(value)
        except ValueError:
            # A value written by an older version. Returning it raw is better
            # than raising while merely reading a row.
            return None


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
