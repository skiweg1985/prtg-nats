"""Database engine and session handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite(dbapi_connection: Any, _: Any) -> None:
    """SQLite needs three pragmas to behave like a database under concurrency.

    WAL lets the job workers write while the API reads. busy_timeout turns the
    "database is locked" error into a short wait, which is what every caller
    would do anyway. foreign_keys is off by default in SQLite, which would
    silently ignore every ON DELETE CASCADE in the schema.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    url = settings.effective_database_url
    engine = create_async_engine(
        url,
        echo=settings.debug and settings.environment != "production",
        future=True,
        # SQLite ignores pool size, PostgreSQL later will not.
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(settings)
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("database engine is not initialised")
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, committed on success.

    Routes do not call commit themselves. Either the request succeeds and its
    writes land together, or an exception rolls all of them back - including
    the audit record, which must not survive an action that did not happen.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Same contract, for background work that has no request around it."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
