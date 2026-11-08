"""The setup code from the recovery command line, as `prtg-nats setup` prints it."""

from __future__ import annotations

import argparse
import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import ops
from app.core.config import Settings
from app.persistence import session as session_module
from app.services.auth import AuthService
from app.services.setup_code import SetupCodeStore

CODE = re.compile(r"Setup code: ([0-9A-Z]{4}(?:-[0-9A-Z]{4}){3})")


@pytest_asyncio.fixture(autouse=True)
async def wired(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    monkeypatch.setattr(ops, "_settings", lambda: settings)
    # The command runs as a process of its own, where nothing has opened the
    # database yet. The fixtures have; closing it here puts that state back,
    # while the factory the test holds still reaches the same file.
    await session_module.dispose_engine()


async def test_prints_a_code_the_store_accepts(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    await ops._cmd_setup_code(argparse.Namespace(if_needed=True))

    match = CODE.search(capsys.readouterr().out)
    assert match, "no code printed"
    assert SetupCodeStore(settings).matches(match.group(1))


async def test_if_needed_stays_quiet_once_an_account_exists(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with session_factory() as db:
        await AuthService(db, settings).create_initial_administrator(
            "admin", "correct-horse-battery"
        )
        await db.commit()

    await ops._cmd_setup_code(argparse.Namespace(if_needed=True))

    assert capsys.readouterr().out == ""
    assert not SetupCodeStore(settings).path.exists()


async def test_without_if_needed_a_code_is_always_issued(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    capsys: pytest.CaptureFixture[str],
) -> None:
    await ops._cmd_setup_code(argparse.Namespace(if_needed=False))

    assert CODE.search(capsys.readouterr().out)
