"""The setup code from the recovery command line, as `prtg-nats setup` prints it."""

from __future__ import annotations

import argparse
import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import ops
from app.core.config import Settings
from app.services.auth import AuthService
from app.services.setup_code import SetupCodeStore

CODE = re.compile(r"Setup code: ([0-9A-Z]{4}(?:-[0-9A-Z]{4}){3})")


@pytest.fixture(autouse=True)
def wired(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(ops, "_settings", lambda: settings)


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
