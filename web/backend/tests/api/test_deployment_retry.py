"""A deployment retry gets a row of its own, and only the probes that failed.

The naive retry copied the whole payload: it re-ran the finished probes and
wrote its outcome into the original deployment's targets, while the row's
job link kept pointing at the first, failed job - two runs stacked on one
record with no sign of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ProbeUnreachableError
from app.domain.enums import JobStatus
from app.persistence.models.inventory import (
    Deployment,
    ProbeObservedState,
)
from tests.api.test_jobs import build_runner, drain, sign_in
from tests.conftest import ScriptedTransport, write_probe_inventory, write_sensor


async def test_a_deployment_retry_targets_only_the_failed_probes(
    client: AsyncClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01", host="berlin.example.test")
    write_probe_inventory(project_dir, "mpp-hamburg-01", host="hamburg.example.test")
    write_sensor(project_dir, "internet-speed")

    down = {"mpp-hamburg-01"}

    class PartiallyDown(ScriptedTransport):
        async def run(self, connection, request, timeout):  # type: ignore[no-untyped-def]
            if connection.nats_username in down:
                raise ProbeUnreachableError.of(
                    connection.nats_username, details="connection timed out"
                )
            return await super().run(connection, request, timeout)

    transport = PartiallyDown()
    await sign_in(client)

    probe_ids = [entry["id"] for entry in (await client.get("/api/v1/probes")).json()]
    first = (
        await client.post(
            "/api/v1/deployments",
            json={"sensor": "internet-speed", "probe_ids": probe_ids},
        )
    ).json()
    await drain(build_runner(settings, transport))

    # The host comes back; the retry should reach it and leave berlin alone.
    down.clear()
    retried = await client.post(f"/api/v1/jobs/{first['job_id']}/retry")
    assert retried.status_code == 202, retried.text
    await drain(build_runner(settings, transport))

    async with session_factory() as db:
        rows = (await db.scalars(select(Deployment))).all()
        assert len(rows) == 2, "the retry must not write into the original row"
        original = next(row for row in rows if row.id == first["id"])
        second = next(row for row in rows if row.id != first["id"])

        # The first run stays what it was: half green, linked to its own job.
        assert original.status is JobStatus.PARTIALLY_SUCCESSFUL
        assert original.job_id == first["job_id"]

        assert second.status is JobStatus.SUCCESSFUL
        assert second.job_id == retried.json()["job_id"]
        assert [target.probe_label for target in second.targets] == ["mpp-hamburg-01"]


async def test_the_sensor_detail_names_who_is_behind(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    project_dir: Path,
) -> None:
    """ "Outdated on one" is only useful together with which one."""
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-hamburg-01")
    write_sensor(project_dir, "internet-speed", version="2")
    await sign_in(client)

    listed = (await client.get("/api/v1/probes")).json()
    by_name = {entry["nats_username"]: entry["id"] for entry in listed}
    versions = {"mpp-berlin-01": "2", "mpp-hamburg-01": "1"}
    async with session_factory() as db:
        for username, version in versions.items():
            db.add(
                ProbeObservedState(
                    probe_id=by_name[username],
                    observed_at=datetime.now(UTC),
                    reachable=True,
                    document={
                        "sensors": [{"name": "internet-speed", "version": version}]
                    },
                )
            )
        await db.commit()

    detail = await client.get("/api/v1/sensors/internet-speed")
    assert detail.status_code == 200, detail.text
    installations = detail.json()["installations"]
    assert installations == [
        {"probe": "mpp-berlin-01", "version": "2", "current": True},
        {"probe": "mpp-hamburg-01", "version": "1", "current": False},
    ]
