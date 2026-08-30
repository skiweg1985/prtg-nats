"""Merging two answers about one probe into one state.

Refreshing a probe asks it twice - probe-info, then sensor-list - and folds
the second answer into the first. That merge used to name every field it
carried over, and the two it forgot were enough to make every healthy probe
look like one the platform refuses to talk to.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from app.domain.enums import ServiceState
from app.domain.models import InstalledSensor, ObservedProbeState
from app.infrastructure.probe_helper import CURRENT_HELPER_VERSION
from app.services.probes import _with_sensors


def _fully_populated() -> ObservedProbeState:
    """Every field set to something distinguishable from its default.

    A default that survives being dropped is a field this test cannot speak
    for, so nothing here is left at one.
    """
    return ObservedProbeState(
        nats_username="mpp-berlin-01",
        observed_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        reachable=True,
        service=ServiceState.ACTIVE,
        package_version="3.10.0-1",
        hostname="berlin-probe-01.example.test",
        ca_sha256="ce3ec54b",
        config_path="/etc/paessler/mpprobe/config.yaml",
        probe_id="292bedf4-4a16-4aa5-bd69-430097b50f23",
        probe_name="Berlin",
        has_access_key=True,
        helper_version=CURRENT_HELPER_VERSION,
        helper_sha256="db5ba8ca",
        sensors=(),
        error_code="probe.unreachable",
        error_details="something to carry over",
    )


def test_the_merge_changes_the_sensors_and_nothing_else() -> None:
    """Field by field, because naming them by hand is what went wrong.

    Comparing the whole state rather than the two fields that were dropped:
    the next field added to this state would otherwise be lost in exactly the
    same way, and nothing would say so.
    """
    before = _fully_populated()
    sensors = (InstalledSensor(name="internet-speed", version="2", sha256="aa"),)

    merged = _with_sensors(before, sensors)

    assert merged.sensors == sensors
    assert merged == dataclasses.replace(before, sensors=sensors)


def test_a_probe_reporting_its_helper_version_is_not_called_outdated() -> None:
    """What the operator saw: a freshly enrolled probe told to enrol again.

    The lost helper_version read as a helper from before signed updates - a
    probe carrying no key to verify one against - so the interface greyed out
    the update button and asked for an enrolment that changed nothing.
    """
    observed = _with_sensors(_fully_populated(), ())

    assert observed.helper_version == CURRENT_HELPER_VERSION
    assert observed.helper_sha256 == "db5ba8ca"
    assert observed.helper_outdated is False
