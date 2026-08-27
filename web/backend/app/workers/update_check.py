"""Ask the repository, now and then, whether the branch has moved.

Its own worker rather than a pass inside the inventory sync, because the two
have nothing to do with each other: that one talks SSH to every probe every
minute, this one talks to a git remote once an hour. Sharing a loop would tie
the interval of one to the other and put a network call to a code host in the
middle of a fleet refresh.

The check writes nothing into the checkout. `git ls-remote` answers the only
question it asks, which means a timer can never leave a half-fetched
repository behind for an update to trip over.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.docker import DockerAdapter
from app.persistence.session import session_scope
from app.services.stack_update import StackUpdateService

logger = get_logger(__name__)


class UpdateCheck:
    def __init__(self, *, settings: Settings, docker: DockerAdapter) -> None:
        self._settings = settings
        self._docker = docker
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._settings.update_check_interval_seconds > 0

    async def start(self) -> None:
        if not self.enabled:
            logger.info("update check disabled")
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="update-check")
        logger.info(
            "update check started",
            extra={"interval": self._settings.update_check_interval_seconds},
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never fatal. An installation with no route to its repository
                # is a normal one that simply cannot be told about updates -
                # everything else it does keeps working.
                logger.exception("update check failed")
            await asyncio.sleep(self._settings.update_check_interval_seconds)

    async def run_once(self) -> None:
        """One look, recorded so the interface can answer without waiting.

        Starting a container takes long enough that doing it per page load
        would make the page feel broken. The row this writes is what the page
        actually reads.
        """
        service = StackUpdateService(self._settings, self._docker)
        readiness = await service.readiness()
        if not readiness.available:
            logger.debug("update check skipped", extra={"reason": readiness.reason})
            return

        result = await service.probe()
        async with session_scope() as db:
            await service.record(db, result)

        logger.info(
            "update check completed",
            extra={
                "branch": result.branch,
                "reachable": result.reachable,
                "behind": len(result.commits),
            },
        )
