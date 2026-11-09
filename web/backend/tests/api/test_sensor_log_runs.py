"""Stepping through the runs of one timeline row, and hiding a row.

A record on its own does not know the runs around it; the run viewer on the
sensor page asks for the previous and next run of the same stream. A row of
the timeline can be hidden until its next run, without touching the records.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.probe.deps import get_probe_identity
from tests.conftest import SETUP_CODE, write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-example-01"
DAY = {"since": "2025-03-08T00:00:00Z", "until": "2025-03-08T23:59:59Z"}


def run(
    number: int,
    *,
    result: str = "ok",
    ssid: str = "Example-Corp",
    sensor: str = "wlan-auth",
    minute: int | None = None,
    job: str = "a1b2c3d4e5f6",
) -> dict[str, Any]:
    """Run ``number`` of a stream, ``minute`` minutes after 09:00."""
    at = number if minute is None else minute
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "sensor": sensor,
        "job": job,
        "time": f"2025-03-08T09:{at:02d}:00Z",
        "result": result,
        "code": "auth-failed-eap" if result == "error" else "ok",
        "summary": "",
        "body": {"ssid": ssid},
    }


def rid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


@pytest.fixture
def as_probe(app: Any) -> Iterator[None]:
    app.dependency_overrides[get_probe_identity] = lambda: PROBE
    yield
    app.dependency_overrides.pop(get_probe_identity, None)


async def submit(client: AsyncClient, *records: dict[str, Any]) -> None:
    response = await client.post("/probe/v1/records", json={"records": list(records)})
    assert response.status_code == 200, response.text


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup",
        json={"setup_code": SETUP_CODE, "username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text


async def neighbours(
    client: AsyncClient, number: int, *, failures_only: bool = False
) -> tuple[str | None, str | None]:
    response = await client.get(
        f"/api/v1/sensor-logs/{rid(number)}/neighbours",
        params={"failures_only": str(failures_only).lower()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record"]["id"] == rid(number)
    return (
        body["previous"]["id"] if body["previous"] else None,
        body["next"]["id"] if body["next"] else None,
    )


# --- Previous and next run ---------------------------------------------------


async def test_the_neighbours_of_a_run_are_the_runs_on_either_side(
    client: AsyncClient, as_probe: None
) -> None:
    # Submitted out of order: the order is the time, not the arrival.
    await submit(client, run(3), run(1), run(2))
    await sign_in(client)

    assert await neighbours(client, 2) == (rid(1), rid(3))


async def test_both_ends_of_the_history_have_no_neighbour_beyond_them(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(client, run(1), run(2))
    await sign_in(client)

    assert await neighbours(client, 1) == (None, rid(2))
    assert await neighbours(client, 2) == (rid(1), None)


async def test_neighbours_stay_inside_the_stream(
    client: AsyncClient, as_probe: None
) -> None:
    # Another network of the same sensor and another sensor run in between;
    # neither is the previous or next run of the first network.
    await submit(
        client,
        run(1),
        run(2, ssid="Example-Guest"),
        run(3, sensor="aruba-uplink"),
        run(4),
    )
    await sign_in(client)

    assert await neighbours(client, 1) == (None, rid(4))
    assert await neighbours(client, 2) == (None, None)


async def test_failures_only_skips_the_runs_that_passed(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(
        client,
        run(1, result="error"),
        run(2),
        run(3),
        run(4, result="error"),
        run(5),
    )
    await sign_in(client)

    # From a failure to the next failure, over the passed runs between.
    assert await neighbours(client, 1, failures_only=True) == (None, rid(4))
    # A passed run is a valid starting point.
    assert await neighbours(client, 3, failures_only=True) == (rid(1), rid(4))
    assert await neighbours(client, 4, failures_only=True) == (rid(1), None)


async def test_a_gap_is_not_a_run_to_step_to(
    client: AsyncClient, as_probe: None
) -> None:
    gap = {**run(2), "result": "gap", "code": None, "body": {}}
    await submit(client, run(1), gap, run(3))
    await sign_in(client)

    assert await neighbours(client, 1) == (None, rid(3))


async def test_the_neighbours_of_an_unknown_run_are_not_found(
    client: AsyncClient,
) -> None:
    await sign_in(client)
    response = await client.get(f"/api/v1/sensor-logs/{rid(9)}/neighbours")
    assert response.status_code == 404


# --- The run a timeline slice opens ------------------------------------------


async def run_at(client: AsyncClient, since: str, until: str) -> Any:
    return await client.get(
        "/api/v1/sensor-logs/timeline/run",
        params={
            "probe": PROBE,
            "sensor": "wlan-auth",
            "stream": "Example-Corp",
            "since": since,
            "until": until,
        },
    )


async def test_a_slice_opens_its_first_failure(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(
        client,
        run(1, minute=1),
        run(2, minute=3, result="error"),
        run(3, minute=5, result="error"),
        run(4, minute=7, ssid="Example-Guest", result="error"),
    )
    await sign_in(client)

    response = await run_at(client, "2025-03-08T09:00:00Z", "2025-03-08T09:15:00Z")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == rid(2)


async def test_a_slice_without_a_failure_opens_its_first_run(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(client, run(1, minute=1), run(2, minute=3))
    await sign_in(client)

    response = await run_at(client, "2025-03-08T09:00:00Z", "2025-03-08T09:15:00Z")
    assert response.json()["id"] == rid(1)


async def test_a_slice_without_a_run_opens_the_run_it_is_drawn_from(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(client, run(1, minute=1), run(2, minute=5))
    await sign_in(client)

    response = await run_at(client, "2025-03-08T09:15:00Z", "2025-03-08T09:30:00Z")
    assert response.json()["id"] == rid(2)

    before = await run_at(client, "2025-03-08T08:00:00Z", "2025-03-08T08:15:00Z")
    assert before.status_code == 404


# --- Hiding a row -------------------------------------------------------------


async def timeline_rows(client: AsyncClient) -> list[str]:
    response = await client.get(
        "/api/v1/sensor-logs/timeline",
        params={"probe": PROBE, "sensor": "wlan-auth", **DAY},
    )
    assert response.status_code == 200, response.text
    return [stream["stream"] for stream in response.json()["streams"]]


async def hide(client: AsyncClient, stream: str, sensor: str = "wlan-auth") -> Any:
    return await client.post(
        "/api/v1/sensor-logs/hidden-streams",
        json={"probe": PROBE, "sensor": sensor, "stream": stream},
    )


async def enrolled(client: AsyncClient, project_dir: Path) -> str:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    listing = await client.get("/api/v1/probes")
    return str(listing.json()[0]["id"])


async def test_a_hidden_row_leaves_the_timeline_and_is_audited(
    client: AsyncClient, as_probe: None, project_dir: Path
) -> None:
    await submit(client, run(1), run(2, ssid="Example-Guest"))
    probe_id = await enrolled(client, project_dir)
    assert await timeline_rows(client) == ["Example-Corp", "Example-Guest"]

    response = await hide(client, "Example-Guest")
    assert response.status_code == 200, response.text
    assert response.json()["stream"] == "Example-Guest"

    assert await timeline_rows(client) == ["Example-Corp"]
    # The records stay in the sensor log.
    listed = await client.get(
        "/api/v1/sensor-logs", params={**DAY, "ssid": "Example-Guest"}
    )
    assert [item["id"] for item in listed.json()["items"]] == [rid(2)]

    events = await client.get(
        "/api/v1/audit-events",
        params={"action": "sensor_log.hide_stream", "object_id": probe_id},
    )
    recorded = events.json()
    assert len(recorded) == 1
    assert recorded[0]["after_state"]["stream"] == "Example-Guest"


async def test_a_hidden_row_returns_with_its_next_run(
    client: AsyncClient, as_probe: None, project_dir: Path
) -> None:
    await submit(client, run(1, ssid="Example-Guest"))
    await enrolled(client, project_dir)
    assert (await hide(client, "Example-Guest")).status_code == 200
    assert await timeline_rows(client) == []

    # A run that is older than the hide - a buffered probe catching up - does
    # not bring the row back. The records here are from 2025, the hide is now.
    await submit(client, run(2, ssid="Example-Guest"))
    assert await timeline_rows(client) == []

    # Filed under its arrival - a clock that far ahead is not believed - and
    # so newer than the hide.
    newer = {**run(3, ssid="Example-Guest"), "time": "2099-01-01T00:00:00Z"}
    await submit(client, newer)
    current = await client.get(
        "/api/v1/sensor-logs/timeline", params={"probe": PROBE, "sensor": "wlan-auth"}
    )
    assert [stream["stream"] for stream in current.json()["streams"]] == [
        "Example-Guest"
    ]


async def test_hiding_is_scoped_to_one_sensor_and_stream(
    client: AsyncClient, as_probe: None, project_dir: Path
) -> None:
    await submit(
        client,
        run(1),
        run(2, ssid="Example-Guest"),
        run(3, sensor="aruba-uplink", ssid="Example-Guest"),
    )
    await enrolled(client, project_dir)
    assert (await hide(client, "Example-Guest")).status_code == 200

    assert await timeline_rows(client) == ["Example-Corp"]
    overview = await client.get(
        "/api/v1/sensor-logs/timeline", params={"probe": PROBE, **DAY}
    )
    assert [
        (stream["sensor"], stream["stream"]) for stream in overview.json()["streams"]
    ] == [("aruba-uplink", "Example-Guest"), ("wlan-auth", "Example-Corp")]


async def test_the_timeline_names_the_configuration_behind_the_newest_run(
    client: AsyncClient, as_probe: None
) -> None:
    await submit(client, run(1, job="0f0e0d0c0b0a"), run(2, job="a1b2c3d4e5f6"))
    await sign_in(client)
    response = await client.get(
        "/api/v1/sensor-logs/timeline",
        params={"probe": PROBE, "sensor": "wlan-auth", **DAY},
    )
    assert response.json()["streams"][0]["last_job"] == "a1b2c3d4e5f6"


async def test_hiding_on_an_unknown_probe_is_not_found(client: AsyncClient) -> None:
    await sign_in(client)
    response = await hide(client, "Example-Guest")
    assert response.status_code == 404
