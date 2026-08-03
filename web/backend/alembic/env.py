"""Alembic environment.

Reads the database URL from the application settings rather than alembic.ini,
so a migration cannot run against a different database than the service.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Imported for the side effect of registering every model.
import app.persistence.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.persistence.base import Base, EnumString, UtcDateTime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().effective_database_url)


def render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    """Render application column types as their plain SQL equivalent.

    A migration that imports app.persistence.base stops working the moment that
    module is renamed, and it gains nothing: EnumString is text in the database
    and UtcDateTime is a timestamp. Autogenerate emits the plain types instead.
    """
    if type_ == "type":
        if isinstance(obj, EnumString):
            autogen_context.imports.add("import sqlalchemy as sa")
            return f"sa.String(length={obj.length})"
        if isinstance(obj, UtcDateTime):
            autogen_context.imports.add("import sqlalchemy as sa")
            return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, which is the only way a column change lands there.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
