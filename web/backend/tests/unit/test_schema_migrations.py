"""The schema is Alembic's, in every state a real installation can be in.

Nothing used to run the migrations. Startup created the missing tables and
stopped there, which is indistinguishable from correct until a migration adds
a column to a table that already exists - and then every request that reads
that table answers 500, on an installation updated exactly as documented.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.core.config import Settings
from app.persistence.base import Base
from app.persistence.schema import _alembic_config, ensure_schema
from app.persistence.session import dispose_engine, init_engine

TABLE = "probe_observed_state"
# The column that exposed all of this: the first one a migration added to a
# table create_all had already made.
COLUMN = "refresh_due"


@pytest_asyncio.fixture
async def engine(settings: Settings, project_dir: Path) -> AsyncIterator[AsyncEngine]:
    """An engine on an empty database file.

    Deliberately not the session_factory fixture: that one builds the schema
    with create_all, which is the state under test rather than the setup.
    """
    engine = init_engine(settings)
    yield engine
    await dispose_engine()


def head() -> str:
    revision = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert revision is not None
    return revision


def columns_of(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


async def stamped_at(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(
            lambda sync: set(inspect(sync).get_table_names())
        )
        if "alembic_version" not in tables:
            return None
        result = await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )
        row = result.first()
    return None if row is None else str(row[0])


async def test_an_empty_database_is_built_by_the_migrations(
    engine: AsyncEngine,
) -> None:
    """Not by create_all: a schema Alembic did not build is one it cannot
    reason about later, which is how the whole thing started."""
    await ensure_schema(engine)

    assert await stamped_at(engine) == head()
    async with engine.connect() as connection:
        assert COLUMN in await connection.run_sync(columns_of, TABLE)


async def test_a_database_a_release_behind_is_brought_forward(
    engine: AsyncEngine,
) -> None:
    """The everyday case, and the one that failed in production."""
    await ensure_schema(engine)
    # Back to the revision an installation running the previous version has.
    # In a thread for the same reason ensure_schema uses one: env.py drives
    # the async engine with asyncio.run, which a running loop refuses.
    await asyncio.to_thread(command.downgrade, _alembic_config(), "-1")
    async with engine.connect() as connection:
        assert COLUMN not in await connection.run_sync(columns_of, TABLE)

    await ensure_schema(engine)

    assert await stamped_at(engine) == head()
    async with engine.connect() as connection:
        assert COLUMN in await connection.run_sync(columns_of, TABLE)


async def test_a_database_create_all_built_is_adopted(engine: AsyncEngine) -> None:
    """Every installation from before this ran. Its schema matches the models,
    so it is at head - it just has nothing saying so."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    assert await stamped_at(engine) is None

    await ensure_schema(engine)

    assert await stamped_at(engine) == head()


async def test_a_database_that_no_longer_matches_the_models_is_refused(
    engine: AsyncEngine,
) -> None:
    """Unversioned and behind. Alembic cannot tell how far behind, and a guess
    would skip the migrations that would have fixed it - quietly, until a
    query hits the missing column. Better to stop with the reason."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"))

    with pytest.raises(RuntimeError) as refusal:
        await ensure_schema(engine)

    assert COLUMN in str(refusal.value)
    assert "stamp" in str(refusal.value).lower()
    # Nothing was written down about a database nobody could place.
    assert await stamped_at(engine) is None
