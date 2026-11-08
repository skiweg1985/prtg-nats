"""Forget sensor logs once they are older than the retention.

Its own worker, like the update check: the inventory sync talks to every probe
every minute, and tying a once-a-day file sweep to that loop would make its
interval depend on a setting that has nothing to do with it.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.sensor_logs import SensorLogStore

logger = get_logger(__name__)

# Once a day is what the retention is counted in. The first pass runs at
# startup, so a platform that was down over a day boundary catches up at once.
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


class SensorLogCleanup:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="sensor-log-cleanup")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never fatal: a sweep that fails leaves files a day longer,
                # which is nothing next to stopping the loop for good.
                logger.exception("sensor log cleanup failed")
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    async def run_once(self) -> int:
        removed = await asyncio.to_thread(SensorLogStore(self._settings).remove_expired)
        if removed:
            logger.info(
                "removed expired sensor logs",
                extra={
                    "files": removed,
                    "retention_days": self._settings.sensor_log_retention_days,
                },
            )
        return removed
