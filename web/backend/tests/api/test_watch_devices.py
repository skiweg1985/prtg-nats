"""The watch list over HTTP: managing devices, and reading the dashboard.

The dashboard is meant to be readable by people who never touch a probe, so
what a viewer may do and what an operator may do is part of the feature
rather than an afterthought.
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


async def _probe_id(client: AsyncClient, project_dir: Path, account: str) -> str:
    from tests.conftest import write_probe_inventory

    write_probe_inventory(project_dir, account)
    listed = (await client.get("/api/v1/probes")).json()
    return next(row["id"] for row in listed if row["nats_username"] == account)


async def _add(
    client: AsyncClient, probe_id: str, name: str, address: str, **extra: object
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/watch/devices",
        json={
            "display_name": name,
            "address": address,
            "probe_id": probe_id,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_a_new_device_is_unknown_rather_than_down(
    client: AsyncClient, project_dir: Path
) -> None:
    """Nobody has measured it yet, and that is not the same as switched off."""
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")

    device = await _add(client, probe_id, "Kassendrucker 1", "10.10.0.31")

    assert device["state"] == "unknown"
    assert device["stale"] is True
    assert device["observed_at"] is None
    assert device["method"] == "icmp"


async def test_a_tcp_check_without_a_port_is_refused(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")

    response = await client.post(
        "/api/v1/watch/devices",
        json={
            "display_name": "Terminal",
            "address": "10.10.0.55",
            "probe_id": probe_id,
            "method": "tcp",
        },
    )
    assert response.status_code == 422, response.text


async def test_switching_to_tcp_without_a_port_is_refused_too(
    client: AsyncClient, project_dir: Path
) -> None:
    """The check has to hold for the device as edited, not as created."""
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")
    device = await _add(client, probe_id, "Terminal", "10.10.0.55")

    response = await client.patch(
        f"/api/v1/watch/devices/{device['id']}", json={"method": "tcp"}
    )
    assert response.status_code == 422, response.text


async def test_the_overview_counts_and_lists_the_labels(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")
    await _add(
        client,
        probe_id,
        "Kassendrucker 1",
        "10.10.0.31",
        labels={"team": "support", "site": "hamburg"},
    )
    await _add(
        client,
        probe_id,
        "EC-Terminal 2",
        "10.10.0.55",
        labels={"team": "kasse", "site": "hamburg"},
    )

    overview = (await client.get("/api/v1/watch/overview")).json()

    assert overview["unknown"] == 2
    assert overview["up"] == 0
    assert overview["down"] == 0
    assert overview["labels"]["team"] == ["kasse", "support"]
    assert overview["labels"]["site"] == ["hamburg"]
    # Nothing is connected in a test process, and the interface says so
    # rather than leaving a wall of unknown devices unexplained.
    assert overview["receiving"] is False


async def test_a_team_sees_only_its_own_devices(
    client: AsyncClient, project_dir: Path
) -> None:
    """The filter the whole dashboard is built around."""
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")
    await _add(
        client, probe_id, "Kassendrucker 1", "10.10.0.31", labels={"team": "support"}
    )
    await _add(
        client, probe_id, "EC-Terminal 2", "10.10.0.55", labels={"team": "kasse"}
    )

    filtered = (
        await client.get("/api/v1/watch/overview", params={"label": "team:kasse"})
    ).json()

    assert [device["display_name"] for device in filtered["devices"]] == [
        "EC-Terminal 2"
    ]
    assert filtered["unknown"] == 1


async def test_a_device_can_be_moved_to_another_probe(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    hamburg = await _probe_id(client, project_dir, "mpp-hamburg-01")
    berlin = await _probe_id(client, project_dir, "mpp-berlin-01")
    device = await _add(client, hamburg, "Kassendrucker 1", "10.10.0.31")

    moved = await client.patch(
        f"/api/v1/watch/devices/{device['id']}", json={"probe_id": berlin}
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["probe_id"] == berlin


async def test_an_unknown_probe_is_a_404(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    response = await client.post(
        "/api/v1/watch/devices",
        json={
            "display_name": "Nowhere",
            "address": "10.0.0.1",
            "probe_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ",
        },
    )
    assert response.status_code == 404, response.text


async def test_deleting_a_device_takes_its_history_with_it(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")
    device = await _add(client, probe_id, "Kassendrucker 1", "10.10.0.31")

    removed = await client.delete(f"/api/v1/watch/devices/{device['id']}")
    assert removed.status_code == 204

    gone = await client.get(f"/api/v1/watch/devices/{device['id']}")
    assert gone.status_code == 404


async def test_availability_of_a_device_nobody_measured_has_no_percentage(
    client: AsyncClient, project_dir: Path
) -> None:
    await _sign_in(client)
    probe_id = await _probe_id(client, project_dir, "mpp-hamburg-01")
    device = await _add(client, probe_id, "Kassendrucker 1", "10.10.0.31")

    summary = (
        await client.get(f"/api/v1/watch/devices/{device['id']}/availability")
    ).json()

    assert summary["ratio"] is None
    assert summary["outages"] == 0
