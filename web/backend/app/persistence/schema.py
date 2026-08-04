"""Bring the database to the schema this code expects.

Alembic owns the schema, and for a while nothing ran it. The service created
the missing tables at startup and left it at that, which works exactly as long
as every change is a new table - create_all makes those too. The first
migration that added a column to an existing table therefore arrived as a
service answering 500 to every request that read that table, on an
installation updated exactly as documented.

Migrations run here instead, in the one process that owns the database and
before anything else touches it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, Connection, Table, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.core.logging import get_logger
from app.persistence.base import Base

# Imported for the side effect of registering every model with the metadata.
# Without it the comparison below would call every table nobody happened to
# import "missing from the models" and refuse to start over it.
from app.persistence import models as _models  # noqa: F401  # isort: skip

logger = get_logger(__name__)

# app/persistence/schema.py -> the backend root, where alembic lives.
SCRIPT_LOCATION = Path(__file__).resolve().parents[2] / "alembic"

ADOPTION_DOCS = "docs/web/install.md#a-database-from-before-migrations-ran"


async def ensure_schema(engine: AsyncEngine) -> None:
    """Migrate to head, whatever state the database is in.

    Three states, and the last one is the reason this is not one line:

    * **Versioned.** Alembic knows where it stands. Upgrade.
    * **Empty.** A fresh installation, built from the migrations rather than
      from ``create_all``. A schema Alembic did not build is a schema Alembic
      cannot reason about, which is the state that produced the third case.
    * **Built by create_all.** Every installation from before this ran. If its
      schema still matches the models it is at head and can be stamped as
      such. If it does not, only the operator knows which release it came
      from - see ``_adopt``.
    """
    config = _alembic_config()
    async with engine.connect() as connection:
        tables = await connection.run_sync(_table_names)

    if tables and "alembic_version" not in tables:
        await _adopt(engine, config)

    # Also after adopting, where it has nothing left to do. One call for all
    # three paths beats three that have to stay in step.
    await asyncio.to_thread(command.upgrade, config, "head")
    if not tables:
        logger.info("built the database from the migrations")


async def _adopt(engine: AsyncEngine, config: Config) -> None:
    """Take a create_all database under Alembic's management, or refuse to.

    Refusing is the point. Alembic cannot tell a schema that is current from
    one that is two releases behind - both are simply unversioned - and
    stamping the wrong one skips the migrations that would have fixed it,
    quietly, until a query hits the missing column. Comparing against the
    models answers the only question that can be answered here: whether there
    is anything left to migrate at all.

    A startup that stops with the reason in the log is a worse morning than a
    startup that works, and a much better one than a service that answers 500
    to every request until somebody reads a traceback.
    """
    async with engine.connect() as connection:
        differences = await connection.run_sync(_differences_from_models)

    if differences:
        raise RuntimeError(
            "this database was not built by a migration and no longer matches "
            "the models, so there is no telling which migrations it still "
            f"needs: {differences}. Stamp it with the revision the previous "
            f"version shipped, then upgrade from there - see {ADOPTION_DOCS}"
        )

    await asyncio.to_thread(command.stamp, config, "head")
    logger.warning(
        "adopted a database that predates migrations running at startup; "
        "its schema already matched the models"
    )


def _alembic_config() -> Config:
    """Alembic's configuration, assembled rather than read from alembic.ini.

    Handing over the file would have env.py pass the ini's ``[logger_*]``
    sections to ``fileConfig``, which resets logging - including the loggers
    this service configured moments ago, which it would then disable. The only
    thing a migration run needs from here is where the scripts are; env.py
    takes the database URL from the settings itself, so a migration cannot run
    against a different database than the service.
    """
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def _differences_from_models(connection: Connection) -> list[str]:
    """What the models have that the database does not, and the reverse."""
    context = MigrationContext.configure(connection)
    return [
        _describe(difference)
        for entry in compare_metadata(context, Base.metadata)
        for difference in (entry if isinstance(entry, list) else [entry])
    ]


def _describe(difference: Any) -> str:
    """One of compare_metadata's tuples, short enough for a log line."""
    if not isinstance(difference, tuple) or not difference:
        return str(difference)
    named = [part for part in difference[1:] if isinstance(part, str)]
    named += [part.name for part in difference[1:] if isinstance(part, Table)]
    named += [part.name for part in difference[1:] if isinstance(part, Column)]
    return " ".join([str(difference[0]), ".".join(named)]).strip()
