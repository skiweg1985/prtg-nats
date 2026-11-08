"""The dashboard and the probe list tell the same story.

A design review asked whether the two could disagree in a real installation -
it had seen "10 unreachable" on one and a single red row on the other. That
came from mock data, but the question stands: both answers have to come from
the one status computation, ``ProbeService.list_summaries``, or they drift
the first time either is changed. This pins the numbers, not the plumbing.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from httpx import AsyncClient

from tests.conftest import SETUP_CODE, write_probe_inventory

PASSWORD = "correct-horse-battery"


async def test_every_dashboard_count_matches_the_list(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-hamburg-01", host="hamburg.example.test")
    write_probe_inventory(project_dir, "mpp-koeln-01", host="koeln.example.test")
    response = await client.post(
        "/api/v1/auth/setup",
        json={"setup_code": SETUP_CODE, "username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text

    listed = (await client.get("/api/v1/probes")).json()
    dashboard = (await client.get("/api/v1/dashboard")).json()

    by_status = Counter(row["status"] for row in listed)
    assert dashboard["probe_total"] == len(listed)
    assert dashboard["probe_healthy"] == by_status["healthy"]
    assert dashboard["probe_degraded"] == by_status["degraded"]
    assert dashboard["probe_unreachable"] == by_status["unreachable"]
    assert dashboard["probe_pending"] == by_status["pending"] + by_status["enrolled"]
    assert dashboard["probe_prtg_missing"] == sum(
        1
        for row in listed
        if not row["prtg_registered"] and row["status"] not in ("pending", "enrolled")
    )
    assert dashboard["probes_with_deviations"] == sum(
        1 for row in listed if row["deviation_count"]
    )
