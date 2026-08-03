"""What a job handler is handed.

Assembled once per job so a handler never reaches for a global, and so a test
can build the same object with fakes and exercise the handler end to end
without a probe, a socket or a database file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.enums import LogLevel
from app.infrastructure.docker import DockerAdapter
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import SensorCatalog
from app.persistence.models.jobs import Job
from app.services.jobs import JobService


@dataclass(slots=True)
class JobContext:
    job: Job
    jobs: JobService
    db: AsyncSession
    settings: Settings
    runtime: RuntimeFileStore
    helper: ProbeHelperClient
    catalog: SensorCatalog
    docker: DockerAdapter
    # Values that must not be persisted - an SSH bootstrap password, for
    # instance. Lives for the duration of the run and nowhere else.
    secrets: dict[str, str]

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    @property
    def cancelled(self) -> bool:
        return bool(self.job.cancel_requested)

    async def log(
        self,
        code: str,
        *,
        level: LogLevel = LogLevel.INFO,
        params: dict[str, Any] | None = None,
        target: str | None = None,
        raw: str | None = None,
    ) -> None:
        await self.jobs.log(
            self.job, code, level=level, params=params, target=target, raw=raw
        )

    async def step(self, name: str) -> None:
        await self.jobs.start_step(self.job, name)
