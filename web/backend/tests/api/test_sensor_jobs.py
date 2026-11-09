"""The jobs a sensor's own helper measures on a probe, and removing one.

Every configuration a wlan-auth sensor in PRTG asks for is a job in the
helper on the probe. A changed parameter makes a new one, and the old one
used to be measured on for an hour. The probe now stops measuring it after
three intervals unasked, and the platform shows what is there and lets
somebody remove a leftover at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.deps.common import get_probe_helper
from app.infrastructure.probe_helper import ProbeHelperClient
from app.infrastructure.probe_helper.protocol import HelperCommand
from tests.conftest import SETUP_CODE, ScriptedTransport, write_probe_inventory

PROBE = "mpp-berlin-01"
PASSWORD = "correct-horse-battery"

# Two jobs: the current configuration and an idle leftover. The SSID goes
# out as hex because a network name may hold a tab or an equals sign.
JOBS = (
    "OK sensor-jobs\n"
    "a1b2c3d4e5f6\tinterface=wlan0\tssid_hex="
    + b"Simplicity Guest".hex()
    + "\tstage=dhcp\tresult=ok\tcode=ok\tage=31.0\tasked_every=60.0"
    "\tasked_ago=12.0\tinterval=60.0\tidle=no\texpires_in=3588.0\tsensor_id=2041\n"
    "0f0e0d0c0b0a\tinterface=wlan0\tssid_hex="
    + b"Simplicity Guest".hex()
    + "\tstage=dhcp\tresult=failed\tcode=auth-failed-eap\tage=400.0"
    "\tasked_every=60.0\tasked_ago=400.0\tinterval=60.0\tidle=yes"
    "\texpires_in=3200.0\tsensor_id=-\n"
)


@pytest.fixture
def helper(app: Any, transport: ScriptedTransport) -> Iterator[ScriptedTransport]:
    app.dependency_overrides[get_probe_helper] = lambda: ProbeHelperClient(transport)
    yield transport
    app.dependency_overrides.pop(get_probe_helper, None)


async def sign_in(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/setup",
        json={"setup_code": SETUP_CODE, "username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text


async def probe_id(client: AsyncClient) -> str:
    listing = await client.get("/api/v1/probes")
    return str(listing.json()[0]["id"])


async def test_the_jobs_of_a_sensor_are_read_from_the_probe(
    client: AsyncClient, helper: ScriptedTransport, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    helper.responses[HelperCommand.SENSOR_JOBS.value] = JOBS
    await sign_in(client)

    response = await client.get(
        f"/api/v1/probes/{await probe_id(client)}/sensors/wlan-auth/jobs"
    )
    assert response.status_code == 200, response.text
    current, leftover = response.json()
    assert current["label"] == "a1b2c3d4e5f6"
    assert current["ssid"] == "Simplicity Guest"
    assert current["idle"] is False
    assert current["sensor_id"] == "2041"
    assert current["asked_every"] == 60.0
    assert leftover["idle"] is True
    assert leftover["code"] == "auth-failed-eap"
    assert leftover["sensor_id"] is None
    _, request = helper.calls[-1]
    assert request.command is HelperCommand.SENSOR_JOBS
    assert request.arguments == ("wlan-auth",)


async def test_a_leftover_job_is_removed_and_audited(
    client: AsyncClient, helper: ScriptedTransport, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    helper.responses[HelperCommand.SENSOR_FORGET_JOB.value] = (
        "OK sensor-job-forgotten removed=1\n"
    )
    await sign_in(client)
    identifier = await probe_id(client)

    response = await client.delete(
        f"/api/v1/probes/{identifier}/sensors/wlan-auth/jobs/0f0e0d0c0b0a"
    )
    assert response.status_code == 204, response.text
    _, request = helper.calls[-1]
    assert request.command is HelperCommand.SENSOR_FORGET_JOB
    assert request.arguments == ("wlan-auth", "0f0e0d0c0b0a")

    events = await client.get(
        "/api/v1/audit-events",
        params={"action": "sensor.forget_job", "object_id": identifier},
    )
    recorded = events.json()
    assert len(recorded) == 1
    assert recorded[0]["object_label"] == PROBE


async def test_a_label_that_is_none_never_reaches_the_probe(
    client: AsyncClient, helper: ScriptedTransport, project_dir: Path
) -> None:
    write_probe_inventory(project_dir, PROBE)
    await sign_in(client)
    response = await client.delete(
        f"/api/v1/probes/{await probe_id(client)}/sensors/wlan-auth/jobs/..%2Fx"
    )
    assert response.status_code in (404, 422)
    assert HelperCommand.SENSOR_FORGET_JOB.value not in helper.commands()
