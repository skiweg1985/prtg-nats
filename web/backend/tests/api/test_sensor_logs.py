"""Sensor logs: what a probe sends over its return channel, and reading it back.

The probe's identity is established by the TLS layer in front of the API and
handed to the routes by get_probe_identity. These tests stand in for that layer
by overriding the dependency, the way the other API tests stand in for the
probe transport; the tests that keep the real dependency check that it honours
the header and the inventory.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.probe.deps import get_probe_identity
from app.core.config import Settings
from app.services.events import get_broadcaster, sensor_log_topic
from app.workers.sensor_log_cleanup import SensorLogCleanup
from tests.conftest import write_probe_inventory

PASSWORD = "correct-horse-battery"
PROBE = "mpp-berlin-01"

RECORD_ID = "0b6f3c1e-8f0a-4d7e-9a43-6a2f1c9e5d10"

# The day the records below were measured. Spelled out in every query so the
# tests do not depend on the clock of the machine running them.
DAY = {"since": "2026-11-08T00:00:00Z", "until": "2026-11-08T23:59:59Z"}


def record(
    record_id: str = RECORD_ID,
    *,
    time: str = "2026-11-08T09:15:00Z",
    result: str = "error",
    sensor: str = "wlan-auth",
    ssid: str = "Example-Corp",
    raw: str = "CTRL-EVENT-EAP-FAILURE EAP authentication failed",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "sensor": sensor,
        "job": "a1b2c3d4e5f6",
        "time": time,
        "result": result,
        "code": "eap_failure" if result == "error" else None,
        "summary": "EAP authentication failed",
        "body": {"ssid": ssid, "supplicant_lines": [raw]},
        **extra,
    }


@pytest.fixture
def as_probe(app: Any) -> Iterator[None]:
    """Requests arrive as PROBE, as if its client certificate had been verified."""
    app.dependency_overrides[get_probe_identity] = lambda: PROBE
    yield
    app.dependency_overrides.pop(get_probe_identity, None)


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


async def test_a_submitted_record_is_readable_through_list_and_detail(
    client: AsyncClient, as_probe: None
) -> None:
    response = await client.post("/probe/v1/records", json={"records": [record()]})
    assert response.status_code == 200, response.text
    assert response.json() == {"stored": [RECORD_ID], "duplicates": []}

    await sign_in(client)
    listed = await client.get("/api/v1/sensor-logs", params=DAY)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["id"] for item in items] == [RECORD_ID]
    assert items[0]["probe"] == PROBE
    assert items[0]["result"] == "error"

    detail = await client.get(f"/api/v1/sensor-logs/{RECORD_ID}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["body"]["supplicant_lines"] == [
        "CTRL-EVENT-EAP-FAILURE EAP authentication failed"
    ]


async def test_resubmitting_a_batch_stores_nothing_twice(
    client: AsyncClient, as_probe: None, settings: Settings
) -> None:
    other = "5d7e2a90-3c4b-4f1e-8a6d-0e9b8c7a6f51"
    first = await client.post("/probe/v1/records", json={"records": [record()]})
    assert first.json()["stored"] == [RECORD_ID]

    again = await client.post(
        "/probe/v1/records", json={"records": [record(), record(other)]}
    )
    assert again.status_code == 200, again.text
    assert again.json() == {"stored": [other], "duplicates": [RECORD_ID]}

    day_file = settings.sensor_log_dir / PROBE / "wlan-auth" / "2026-11-08.jsonl"
    ids = [json.loads(line)["id"] for line in day_file.read_text().splitlines()]
    assert ids == [RECORD_ID, other]


async def test_records_land_as_json_lines_per_probe_sensor_and_day(
    client: AsyncClient, as_probe: None, settings: Settings
) -> None:
    batch = [
        record("11111111-0000-4000-8000-000000000001"),
        record("11111111-0000-4000-8000-000000000002", time="2026-11-09T00:30:00Z"),
        record(
            "11111111-0000-4000-8000-000000000003",
            sensor="aruba-uplink",
            # Local time on the probe; the day is the UTC day.
            time="2026-11-09T00:30:00+02:00",
        ),
    ]
    response = await client.post("/probe/v1/records", json={"records": batch})
    assert response.status_code == 200, response.text

    root = settings.sensor_log_dir / PROBE
    files = sorted(str(path.relative_to(root)) for path in root.rglob("*.jsonl"))
    assert files == [
        "aruba-uplink/2026-11-08.jsonl",
        "wlan-auth/2026-11-08.jsonl",
        "wlan-auth/2026-11-09.jsonl",
    ]
    line = json.loads((root / "aruba-uplink" / "2026-11-08.jsonl").read_text())
    assert line["time"] == "2026-11-08T22:30:00Z"
    assert line["probe"] == PROBE


async def test_the_identity_in_the_body_is_ignored(
    client: AsyncClient, as_probe: None
) -> None:
    claimed = record(probe="mpp-hamburg-02")
    response = await client.post("/probe/v1/records", json={"records": [claimed]})
    assert response.status_code == 200, response.text

    await sign_in(client)
    detail = await client.get(f"/api/v1/sensor-logs/{RECORD_ID}")
    assert detail.json()["probe"] == PROBE


async def test_a_gap_record_is_stored_and_returned_as_a_gap(
    client: AsyncClient, as_probe: None
) -> None:
    gap = {
        "id": RECORD_ID,
        "sensor": "wlan-auth",
        "time": "2026-11-08T10:00:00Z",
        "result": "gap",
        "summary": "37 records dropped from a full buffer",
        "body": {
            "from": "2026-11-08T06:00:00Z",
            "until": "2026-11-08T09:45:00Z",
            "lost": 37,
        },
    }
    response = await client.post("/probe/v1/records", json={"records": [gap]})
    assert response.status_code == 200, response.text

    await sign_in(client)
    listed = await client.get("/api/v1/sensor-logs", params={**DAY, "result": "gap"})
    assert [item["result"] for item in listed.json()["items"]] == ["gap"]
    detail = await client.get(f"/api/v1/sensor-logs/{RECORD_ID}")
    assert detail.json()["body"]["lost"] == 37


# --- Who may report ----------------------------------------------------------
# No override here: these go through the real get_probe_identity.


async def test_an_enrolled_probe_is_known_by_the_header_the_proxy_sets(
    client: AsyncClient, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    response = await client.post(
        "/probe/v1/records",
        json={"records": [record()]},
        headers={"X-Probe-Identity": f"CN={PROBE}"},
    )
    assert response.status_code == 200, response.text

    await sign_in(client)
    detail = await client.get(f"/api/v1/sensor-logs/{RECORD_ID}")
    assert detail.json()["probe"] == PROBE


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no identity"),
        pytest.param({"X-Probe-Identity": "CN=mpp-unknown-09"}, id="not enrolled"),
        pytest.param({"X-Probe-Identity": "CN=../probes/mpp-berlin-01"}, id="a path"),
    ],
)
async def test_an_unknown_identity_is_rejected(
    client: AsyncClient,
    project_dir: Path,
    settings: Settings,
    headers: dict[str, str],
) -> None:
    write_probe_inventory(project_dir, PROBE)
    response = await client.post(
        "/probe/v1/records", json={"records": [record()]}, headers=headers
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.probe_not_enrolled"
    assert not settings.sensor_log_dir.exists()


async def test_a_retired_probe_is_rejected_on_its_next_request(
    client: AsyncClient, project_dir: Path, settings: Settings
) -> None:
    write_probe_inventory(project_dir, PROBE)
    headers = {"X-Probe-Identity": f"CN={PROBE}"}
    first = await client.post(
        "/probe/v1/records", json={"records": [record()]}, headers=headers
    )
    assert first.status_code == 200, first.text

    (settings.probe_dir / f"{PROBE}.env").unlink()
    again = await client.post(
        "/probe/v1/records",
        json={"records": [record("5d7e2a90-3c4b-4f1e-8a6d-0e9b8c7a6f51")]},
        headers=headers,
    )
    assert again.status_code == 403, again.text


# --- Reading -----------------------------------------------------------------


async def test_reading_needs_a_signed_in_user(
    client: AsyncClient, as_probe: None
) -> None:
    await client.post("/probe/v1/records", json={"records": [record()]})
    for path in (
        "/api/v1/sensor-logs",
        f"/api/v1/sensor-logs/{RECORD_ID}",
        "/api/v1/sensor-logs/events",
    ):
        response = await client.get(path)
        assert response.status_code == 401, (path, response.text)


async def test_each_filter_narrows_the_list(
    client: AsyncClient, as_probe: None
) -> None:
    ids = [f"22222222-0000-4000-8000-00000000000{n}" for n in range(1, 6)]
    batch = [
        record(ids[0], result="ok", ssid="Example-Corp", raw="EAP success"),
        record(ids[1], result="error", ssid="Example-Guest", raw="dhclient: no lease"),
        record(ids[2], result="error", sensor="aruba-uplink", raw="link down"),
        record(ids[3], result="ok", time="2026-11-08T20:00:00Z", raw="EAP success"),
        record(ids[4], result="error", time="2026-11-07T20:00:00Z"),
    ]
    response = await client.post("/probe/v1/records", json={"records": batch})
    assert response.status_code == 200, response.text
    await sign_in(client)

    async def listed(**params: str) -> list[str]:
        answer = await client.get("/api/v1/sensor-logs", params={**DAY, **params})
        assert answer.status_code == 200, answer.text
        return sorted(item["id"] for item in answer.json()["items"])

    assert await listed() == sorted(ids[:4])
    assert await listed(probe=PROBE) == sorted(ids[:4])
    assert await listed(probe="mpp-hamburg-02") == []
    assert await listed(sensor="aruba-uplink") == [ids[2]]
    assert await listed(ssid="Example-Guest") == [ids[1]]
    assert await listed(result="ok") == sorted([ids[0], ids[3]])
    assert await listed(q="NO LEASE") == [ids[1]]
    assert await listed(since="2026-11-08T12:00:00Z") == [ids[3]]
    assert await listed(since="2026-11-07T00:00:00Z") == sorted(ids)
    bad = await client.get("/api/v1/sensor-logs", params={"result": "pending"})
    assert bad.status_code == 422


async def test_the_text_search_matches_raw_output_as_it_was_written(
    client: AsyncClient, as_probe: None
) -> None:
    """Quotes and backslashes are searched for as the supplicant printed
    them, not in the escaped form they take in the stored JSON."""
    quoted = "66666666-0000-4000-8000-000000000001"
    other = "66666666-0000-4000-8000-000000000002"
    batch = [
        record(quoted, raw='wlan0: SSID "Example-Corp" rejected, user DOMAIN\\alice'),
        record(other, raw="wlan0: CTRL-EVENT-DISCONNECTED reason=3"),
    ]
    response = await client.post("/probe/v1/records", json={"records": batch})
    assert response.status_code == 200, response.text
    await sign_in(client)

    async def listed(text: str) -> list[str]:
        answer = await client.get("/api/v1/sensor-logs", params={**DAY, "q": text})
        assert answer.status_code == 200, answer.text
        return [item["id"] for item in answer.json()["items"]]

    assert await listed('SSID "Example-Corp"') == [quoted]
    assert await listed("DOMAIN\\alice") == [quoted]
    # Field names and JSON punctuation are not part of what was written.
    assert await listed("supplicant_lines") == []
    assert await listed('", "') == []


async def test_without_a_time_range_the_last_24_hours_are_listed(
    client: AsyncClient, as_probe: None
) -> None:
    now = datetime.now(UTC)
    recent = "33333333-0000-4000-8000-000000000001"
    old = "33333333-0000-4000-8000-000000000002"
    batch = [
        record(recent, time=(now - timedelta(hours=2)).isoformat()),
        record(old, time=(now - timedelta(hours=30)).isoformat()),
    ]
    await client.post("/probe/v1/records", json={"records": batch})
    await sign_in(client)

    listed = await client.get("/api/v1/sensor-logs")
    assert [item["id"] for item in listed.json()["items"]] == [recent]


async def test_the_list_pages_newest_first(client: AsyncClient, as_probe: None) -> None:
    ids = [f"44444444-0000-4000-8000-00000000000{n}" for n in range(5)]
    batch = [
        record(record_id, time=f"2026-11-08T1{n}:00:00Z")
        for n, record_id in enumerate(ids)
    ]
    await client.post("/probe/v1/records", json={"records": batch})
    await sign_in(client)

    first = await client.get("/api/v1/sensor-logs", params={**DAY, "limit": 2})
    second = await client.get(
        "/api/v1/sensor-logs", params={**DAY, "limit": 2, "offset": 2}
    )
    assert first.json()["total"] == 5
    assert [item["id"] for item in first.json()["items"]] == [ids[4], ids[3]]
    assert [item["id"] for item in second.json()["items"]] == [ids[2], ids[1]]


async def test_an_unknown_record_is_not_found(client: AsyncClient) -> None:
    await sign_in(client)
    response = await client.get(f"/api/v1/sensor-logs/{RECORD_ID}")
    assert response.status_code == 404


# --- Announcing and forgetting -----------------------------------------------


async def test_new_records_are_announced_on_the_event_stream(
    client: AsyncClient, as_probe: None
) -> None:
    broadcaster = get_broadcaster()
    queue = await broadcaster.register(sensor_log_topic())
    try:
        await client.post("/probe/v1/records", json={"records": [record()]})
        # The same batch again is not news.
        await client.post("/probe/v1/records", json={"records": [record()]})
        events = [queue.get_nowait() for _ in range(queue.qsize())]
    finally:
        await broadcaster.unregister(sensor_log_topic(), queue)

    assert [event.kind for event in events] == ["sensor_log.record"]
    assert events[0].payload["id"] == RECORD_ID
    assert events[0].payload["probe"] == PROBE
    assert "body" not in events[0].payload


async def test_cleanup_removes_day_files_older_than_the_retention(
    client: AsyncClient, as_probe: None, settings: Settings
) -> None:
    """Fourteen days of retention keep today and the thirteen days before
    it - fourteen day files, not fifteen."""
    assert settings.sensor_log_retention_days == 14
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    kept = "55555555-0000-4000-8000-000000000001"
    expired = "55555555-0000-4000-8000-000000000002"
    batch = [
        record(kept, time=(today - timedelta(days=13)).isoformat()),
        record(expired, time=(today - timedelta(days=14)).isoformat()),
    ]
    await client.post("/probe/v1/records", json={"records": batch})

    await SensorLogCleanup(settings=settings).run_once()

    await sign_in(client)
    listed = await client.get(
        "/api/v1/sensor-logs",
        params={"since": (today - timedelta(days=30)).isoformat()},
    )
    assert [item["id"] for item in listed.json()["items"]] == [kept]
    remaining = list((settings.sensor_log_dir / PROBE).rglob("*.jsonl"))
    assert len(remaining) == 1
