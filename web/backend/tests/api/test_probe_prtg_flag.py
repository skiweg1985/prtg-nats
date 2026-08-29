"""The one manual state the platform cannot observe.

The access key goes into the PRTG core by hand and the probe is approved
there; a probe nobody ever registered stood green with no number counting
it. The tick is the operator's own record - who, when - and clears again.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

PASSWORD = "correct-horse-battery"


async def _sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


async def test_the_tick_records_who_and_when_and_clears(
    client: AsyncClient, project_dir: Path
) -> None:
    from tests.conftest import write_probe_inventory

    write_probe_inventory(project_dir, "mpp-berlin-01")
    await _sign_in(client)

    listed = (await client.get("/api/v1/probes")).json()
    probe_id = listed[0]["id"]
    assert listed[0]["prtg_registered"] is False

    ticked = await client.patch(
        f"/api/v1/probes/{probe_id}", json={"prtg_registered": True}
    )
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["prtg_registered_by"] == "admin"
    assert ticked.json()["prtg_registered_at"] is not None
    assert ticked.json()["summary"]["prtg_registered"] is True

    # None leaves the tick alone - the PATCH edits names and notes too.
    renamed = await client.patch(
        f"/api/v1/probes/{probe_id}", json={"display_name": "Berlin"}
    )
    assert renamed.json()["summary"]["prtg_registered"] is True

    cleared = await client.patch(
        f"/api/v1/probes/{probe_id}", json={"prtg_registered": False}
    )
    assert cleared.json()["summary"]["prtg_registered"] is False
    assert cleared.json()["prtg_registered_by"] is None


async def test_the_dashboard_counts_who_is_missing_from_prtg(
    client: AsyncClient, project_dir: Path
) -> None:
    from tests.conftest import write_probe_inventory

    write_probe_inventory(project_dir, "mpp-berlin-01")
    write_probe_inventory(project_dir, "mpp-hamburg-01", host="hamburg.example.test")
    await _sign_in(client)

    # Both probes have never been observed: they count as pending, not as
    # "missing from PRTG" - a half-enrolled probe has an earlier problem.
    dashboard = (await client.get("/api/v1/dashboard")).json()
    assert dashboard["probe_pending"] == 2
    assert dashboard["probe_prtg_missing"] == 0
