"""Desired versus actual.

Pure functions, so every branch is reachable without a probe or a database.
This is the logic that decides what "fix deviations" does, and it is the part
that most deserves to be exhaustive.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import (
    DeviationKind,
    DeviationSeverity,
    SensorInstallationStatus,
    ServiceState,
)
from app.domain.models import (
    DesiredProbeState,
    DesiredSensor,
    InstalledSensor,
    ObservedProbeState,
)
from app.domain.reconciliation import (
    build_plan,
    compare_sensors,
    find_deviations,
    needs_attention,
)

CA = "3b" * 32
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def observed(**overrides: object) -> ObservedProbeState:
    defaults: dict[str, object] = {
        "nats_username": "mpp-berlin-01",
        "observed_at": NOW,
        "reachable": True,
        "service": ServiceState.ACTIVE,
        "package_version": "2.1.0",
        "ca_sha256": CA,
        "probe_id": "11111111-2222-3333-4444-555555555555",
        "sensors": (),
    }
    defaults.update(overrides)
    return ObservedProbeState(**defaults)  # type: ignore[arg-type]


def test_sensor_present_at_the_wanted_version_is_current() -> None:
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(
        sensors=(InstalledSensor(name="internet-speed", version="2", sha256="aaaa"),)
    )

    comparisons = compare_sensors(
        desired, state, catalogue_versions={"internet-speed": "2"}
    )
    assert comparisons[0].status is SensorInstallationStatus.CURRENT


def test_missing_sensor_is_absent() -> None:
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    comparisons = compare_sensors(
        desired, observed(), catalogue_versions={"internet-speed": "2"}
    )
    assert comparisons[0].status is SensorInstallationStatus.ABSENT


def test_older_version_is_outdated() -> None:
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(
        sensors=(InstalledSensor(name="internet-speed", version="1", sha256="aaaa"),)
    )
    comparisons = compare_sensors(
        desired, state, catalogue_versions={"internet-speed": "2"}
    )
    assert comparisons[0].status is SensorInstallationStatus.OUTDATED


def test_same_version_different_bytes_is_drift() -> None:
    """Somebody edited the script on the probe, or a rollout stopped halfway.

    Both need the same remedy, and neither is "current" - which is exactly the
    case a version comparison alone would miss.
    """
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(
        sensors=(InstalledSensor(name="internet-speed", version="2", sha256="bbbb"),)
    )
    comparisons = compare_sensors(
        desired,
        state,
        catalogue_versions={"internet-speed": "2"},
        catalogue_checksums={"internet-speed": "aaaa"},
    )
    assert comparisons[0].status is SensorInstallationStatus.DRIFTED


def test_a_pinned_version_wins_over_the_catalogue() -> None:
    desired = DesiredProbeState(
        sensors=(DesiredSensor(name="internet-speed", version="1"),)
    )
    state = observed(
        sensors=(InstalledSensor(name="internet-speed", version="1", sha256="aaaa"),)
    )
    comparisons = compare_sensors(
        desired, state, catalogue_versions={"internet-speed": "2"}
    )
    assert comparisons[0].status is SensorInstallationStatus.CURRENT


def test_unrequested_sensor_is_reported_not_removed() -> None:
    state = observed(
        sensors=(InstalledSensor(name="dns-check", version="1", sha256="cccc"),)
    )
    comparisons = compare_sensors(DesiredProbeState(), state, catalogue_versions={})
    assert comparisons[0].status is SensorInstallationStatus.UNMANAGED

    deviations = find_deviations(
        DesiredProbeState(), state, catalogue_versions={}, expected_ca_sha256=CA
    )
    unmanaged = next(d for d in deviations if d.kind is DeviationKind.SENSOR_UNMANAGED)
    # No remediation: adopting it is the likelier intent, and that is a choice
    # for the operator, not a default.
    assert unmanaged.remediation is None
    # And therefore nothing to raise a warning about, which is the same
    # statement seen from the badge's side: it would never clear.
    assert not needs_attention(unmanaged)


def test_a_finding_with_a_remedy_needs_attention() -> None:
    """The other side of the same rule, so it cannot quieten everything."""
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(service=ServiceState.INACTIVE)

    deviations = find_deviations(
        desired,
        state,
        catalogue_versions={"internet-speed": "2"},
        expected_ca_sha256=CA,
    )

    assert deviations
    assert all(needs_attention(entry) for entry in deviations)
    assert {entry.kind for entry in deviations} == {
        DeviationKind.SERVICE_INACTIVE,
        DeviationKind.SENSOR_MISSING,
    }


def test_an_unreachable_probe_produces_no_findings() -> None:
    """Reporting "sensor missing" about a host we cannot reach is a guess."""
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(reachable=False, service=ServiceState.UNKNOWN)

    assert (
        find_deviations(desired, state, catalogue_versions={"internet-speed": "2"})
        == []
    )


def test_wrong_ca_is_critical() -> None:
    state = observed(ca_sha256="ff" * 32)
    deviations = find_deviations(
        DesiredProbeState(), state, catalogue_versions={}, expected_ca_sha256=CA
    )
    mismatch = next(d for d in deviations if d.kind is DeviationKind.CA_MISMATCHED)
    assert mismatch.severity is DeviationSeverity.CRITICAL
    assert mismatch.remediation == "probe.install_ca"


def test_inactive_service_is_critical() -> None:
    deviations = find_deviations(
        DesiredProbeState(),
        observed(service=ServiceState.INACTIVE),
        catalogue_versions={},
        expected_ca_sha256=CA,
    )
    assert deviations[0].kind is DeviationKind.SERVICE_INACTIVE
    assert deviations[0].severity is DeviationSeverity.CRITICAL


def test_findings_are_ordered_by_severity() -> None:
    desired = DesiredProbeState(
        sensors=(DesiredSensor(name="internet-speed"),), probe_name="Wanted"
    )
    state = observed(service=ServiceState.INACTIVE, probe_name="Actual")
    deviations = find_deviations(
        desired,
        state,
        catalogue_versions={"internet-speed": "2"},
        expected_ca_sha256=CA,
    )
    severities = [d.severity for d in deviations]
    assert severities == sorted(
        severities,
        key=lambda s: {
            DeviationSeverity.CRITICAL: 0,
            DeviationSeverity.WARNING: 1,
            DeviationSeverity.INFO: 2,
        }[s],
    )


def test_plan_fixes_the_foundation_first() -> None:
    """A sensor self-test cannot pass before the CA and the service are right."""
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(service=ServiceState.INACTIVE, ca_sha256=None)
    deviations = find_deviations(
        desired,
        state,
        catalogue_versions={"internet-speed": "2"},
        expected_ca_sha256=CA,
    )
    plan = build_plan("mpp-berlin-01", deviations)

    kinds = [action.kind for action in plan.actions]
    assert kinds.index("restart_service") < kinds.index("install_ca")
    assert kinds.index("install_ca") < kinds.index("deploy_sensor")


def test_plan_flags_a_service_interruption() -> None:
    deviations = find_deviations(
        DesiredProbeState(),
        observed(service=ServiceState.INACTIVE),
        catalogue_versions={},
        expected_ca_sha256=CA,
    )
    plan = build_plan("mpp-berlin-01", deviations)
    assert plan.restarts_service
    assert plan.actions[0].risk_key == "plan.risk.service_interruption"


def test_a_healthy_probe_has_an_empty_plan() -> None:
    desired = DesiredProbeState(sensors=(DesiredSensor(name="internet-speed"),))
    state = observed(
        sensors=(InstalledSensor(name="internet-speed", version="2", sha256="aaaa"),)
    )
    deviations = find_deviations(
        desired,
        state,
        catalogue_versions={"internet-speed": "2"},
        catalogue_checksums={"internet-speed": "aaaa"},
        expected_ca_sha256=CA,
    )
    assert build_plan("mpp-berlin-01", deviations).is_empty


def test_observed_state_survives_a_round_trip_through_json() -> None:
    """It is stored as a document and read back on every list request."""
    state = observed(
        sensors=(
            InstalledSensor(
                name="wlan-auth",
                version="1",
                sha256="bbbb",
                interfaces=("wlan0",),
                helper_state="active",
            ),
        )
    )
    restored = ObservedProbeState.from_document(
        nats_username=state.nats_username,
        observed_at=state.observed_at,
        reachable=state.reachable,
        document=state.to_document(),
    )
    assert restored.sensors == state.sensors
    assert restored.service is state.service
    assert restored.ca_sha256 == state.ca_sha256
