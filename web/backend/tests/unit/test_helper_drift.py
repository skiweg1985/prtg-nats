"""A privileged helper changed on the probe, and whether anyone notices.

A probe reports the digest of its sensor script, and the comparison caught an
edited script with it. The privileged helper had no digest at all - it was
reported as none, listening or inactive, nothing more - so a helper that
differed from the catalogue read as current. That is the half running as
root, where a copy nobody can account for matters most.

It surfaced on wlan-auth: three fixes to the helper, the script untouched,
the version unchanged. Every probe would have shown as up to date while
running a helper with three known faults.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import SensorInstallationStatus
from app.domain.models import (
    DesiredProbeState,
    DesiredSensor,
    InstalledSensor,
    ObservedProbeState,
)
from app.domain.reconciliation import compare_sensors

PROBE = "mpp-berlin-01"
SENSOR = "wlan-auth"
SCRIPT = "1111"
HELPER = "2222"


def observed(installed: InstalledSensor) -> ObservedProbeState:
    return ObservedProbeState(
        nats_username=PROBE,
        observed_at=datetime.now(UTC),
        reachable=True,
        sensors=(installed,),
    )


def compare(
    installed: InstalledSensor, *, expected_helper: str | None = HELPER
) -> SensorInstallationStatus:
    desired = DesiredProbeState(sensors=(DesiredSensor(name=SENSOR),))
    rows = compare_sensors(
        desired,
        observed(installed),
        catalogue_versions={SENSOR: "1"},
        catalogue_checksums={SENSOR: SCRIPT},
        catalogue_helper_checksums=(
            {SENSOR: expected_helper} if expected_helper else {}
        ),
    )
    return rows[0].status


def installed(**overrides: object) -> InstalledSensor:
    values: dict[str, object] = {
        "name": SENSOR,
        "version": "1",
        "sha256": SCRIPT,
        "helper_state": "listening",
        "helper_sha256": HELPER,
    }
    values.update(overrides)
    return InstalledSensor(**values)  # type: ignore[arg-type]


def test_a_helper_that_matches_is_current() -> None:
    assert compare(installed()) is SensorInstallationStatus.CURRENT


def test_a_helper_edited_on_the_probe_is_drift() -> None:
    """Same version, same script, different helper - the case that was missed."""
    assert (
        compare(installed(helper_sha256="deadbeef")) is SensorInstallationStatus.DRIFTED
    )


def test_a_helper_below_version_6_reports_nothing_and_is_not_drift() -> None:
    """Silence is not a difference.

    An older helper omits the field entirely. Reading that as a mismatch
    would mark every probe that has not been updated yet - a warning nobody
    can act on and everybody learns to ignore.
    """
    assert compare(installed(helper_sha256=None)) is SensorInstallationStatus.CURRENT


def test_a_sensor_without_a_helper_is_not_drift() -> None:
    """Most sensors have no privileged helper; the catalogue names none."""
    assert (
        compare(installed(helper_sha256=None), expected_helper=None)
        is SensorInstallationStatus.CURRENT
    )


def test_the_version_still_outranks_the_helper() -> None:
    """A version behind the catalogue is reported as outdated, not as drift.

    Both lead to the same remedy, but they mean different things: one is a
    deployment that has not happened, the other a file somebody changed.
    """
    assert (
        compare(installed(version="0", helper_sha256="deadbeef"))
        is SensorInstallationStatus.OUTDATED
    )


def test_a_drifted_script_is_still_caught() -> None:
    assert compare(installed(sha256="deadbeef")) is SensorInstallationStatus.DRIFTED
