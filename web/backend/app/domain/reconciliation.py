"""Compare what should be true with what is.

Pure functions over the two state documents. No SSH, no database, no clock -
which is why this is the part of the platform that can be tested exhaustively
and the part that decides what "fix deviations" actually does.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import (
    CaState,
    DeviationKind,
    DeviationSeverity,
    SensorInstallationStatus,
    ServiceState,
)
from app.domain.models import (
    DesiredProbeState,
    Deviation,
    InstalledSensor,
    ObservedProbeState,
)


@dataclass(frozen=True, slots=True)
class SensorComparison:
    """The per-sensor row of a probe's sensor tab."""

    name: str
    status: SensorInstallationStatus
    desired_version: str | None
    installed_version: str | None
    installed_sha256: str | None
    expected_sha256: str | None
    interfaces: tuple[str, ...] = ()
    helper_state: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    """What "fix deviations" would do, before it does it.

    Produced by a dry run and shown as a preview. The same structure drives the
    job afterwards, so the preview cannot describe a different set of actions
    than the one that runs.
    """

    probe_username: str
    deviations: tuple[Deviation, ...]
    actions: tuple[PlannedAction, ...]

    @property
    def is_empty(self) -> bool:
        return not self.actions

    @property
    def restarts_service(self) -> bool:
        return any(action.restarts_service for action in self.actions)


@dataclass(frozen=True, slots=True)
class PlannedAction:
    kind: str  # "deploy_sensor" | "remove_sensor" | "install_ca" | ...
    target: str
    description_key: str
    params: dict[str, str]
    restarts_service: bool = False
    # Named so the preview can warn before an operator commits to it.
    risk_key: str | None = None


def compare_sensors(
    desired: DesiredProbeState,
    observed: ObservedProbeState,
    *,
    catalogue_versions: dict[str, str],
    catalogue_checksums: dict[str, str] | None = None,
) -> list[SensorComparison]:
    """Line up wanted sensors against installed ones.

    A desired sensor without an explicit version tracks the catalogue, which is
    what an operator means by "keep it current". Pinning a version is the
    exception and stays honoured.
    """
    checksums = catalogue_checksums or {}
    comparisons: list[SensorComparison] = []
    seen: set[str] = set()

    for wanted in desired.sensors:
        seen.add(wanted.name)
        target_version = wanted.version or catalogue_versions.get(wanted.name)
        installed = observed.sensor(wanted.name)
        comparisons.append(
            SensorComparison(
                name=wanted.name,
                status=_sensor_status(
                    installed, target_version, checksums.get(wanted.name)
                ),
                desired_version=target_version,
                installed_version=installed.version if installed else None,
                installed_sha256=installed.sha256 if installed else None,
                expected_sha256=checksums.get(wanted.name),
                interfaces=installed.interfaces if installed else (),
                helper_state=installed.helper_state if installed else None,
            )
        )

    # Anything on the probe that nobody asked for. Reported, never removed
    # automatically: it may well be there on purpose.
    for installed in observed.sensors:
        if installed.name in seen:
            continue
        comparisons.append(
            SensorComparison(
                name=installed.name,
                status=SensorInstallationStatus.UNMANAGED,
                desired_version=None,
                installed_version=installed.version,
                installed_sha256=installed.sha256,
                expected_sha256=checksums.get(installed.name),
                interfaces=installed.interfaces,
                helper_state=installed.helper_state,
            )
        )

    return sorted(comparisons, key=lambda entry: entry.name)


def _sensor_status(
    installed: InstalledSensor | None,
    target_version: str | None,
    expected_sha256: str | None,
) -> SensorInstallationStatus:
    if installed is None:
        return SensorInstallationStatus.ABSENT
    if target_version and installed.version != target_version:
        return SensorInstallationStatus.OUTDATED
    # Same version, different bytes: someone edited the script on the probe, or
    # a deployment stopped halfway. Both need the same remedy and neither is
    # "current".
    if expected_sha256 and installed.sha256 and installed.sha256 != expected_sha256:
        return SensorInstallationStatus.DRIFTED
    return SensorInstallationStatus.CURRENT


def needs_attention(deviation: Deviation) -> bool:
    """Whether a deviation is something to act on, or something to know about.

    The difference is whether anything the platform can do would resolve it.
    An unrequested sensor and a name that does not match are findings with no
    remedy the platform is entitled to choose - only an operator can decide
    whether to adopt them or remove them, and until they do, both states are
    legitimate. Counting those as problems produces a warning that cannot be
    cleared, and a warning that cannot be cleared is one everybody learns to
    ignore, which costs the ones that matter.
    """
    return deviation.severity is not DeviationSeverity.INFO


def find_deviations(
    desired: DesiredProbeState,
    observed: ObservedProbeState,
    *,
    catalogue_versions: dict[str, str],
    catalogue_checksums: dict[str, str] | None = None,
    expected_ca_sha256: str | None = None,
) -> list[Deviation]:
    """Everything that differs, ordered by how much it hurts."""
    deviations: list[Deviation] = []

    if not observed.reachable:
        # Nothing else can be judged; saying "sensor missing" about a probe we
        # cannot reach would be a guess dressed as a finding.
        return deviations

    if observed.service is not ServiceState.ACTIVE:
        deviations.append(
            Deviation(
                kind=DeviationKind.SERVICE_INACTIVE,
                severity=DeviationSeverity.CRITICAL,
                object_type="probe",
                object_ref=observed.nats_username,
                expected=ServiceState.ACTIVE.value,
                actual=observed.service.value,
                remediation="probe.restart_service",
            )
        )

    if desired.ca_required:
        ca_state = observed.ca_state(expected_ca_sha256)
        if ca_state is CaState.MISSING:
            deviations.append(
                Deviation(
                    kind=DeviationKind.CA_MISSING,
                    severity=DeviationSeverity.CRITICAL,
                    object_type="probe",
                    object_ref=observed.nats_username,
                    remediation="probe.install_ca",
                )
            )
        elif ca_state is CaState.MISMATCHED:
            deviations.append(
                Deviation(
                    kind=DeviationKind.CA_MISMATCHED,
                    severity=DeviationSeverity.CRITICAL,
                    object_type="probe",
                    object_ref=observed.nats_username,
                    expected=expected_ca_sha256,
                    actual=observed.ca_sha256,
                    remediation="probe.install_ca",
                )
            )

    if (
        desired.probe_name
        and observed.probe_name
        and desired.probe_name != observed.probe_name
    ):
        deviations.append(
            Deviation(
                kind=DeviationKind.PROBE_NAME_MISMATCH,
                severity=DeviationSeverity.INFO,
                object_type="probe",
                object_ref=observed.nats_username,
                expected=desired.probe_name,
                actual=observed.probe_name,
                remediation="probe.configure",
            )
        )

    for comparison in compare_sensors(
        desired,
        observed,
        catalogue_versions=catalogue_versions,
        catalogue_checksums=catalogue_checksums,
    ):
        deviation = _sensor_deviation(observed.nats_username, comparison)
        if deviation is not None:
            deviations.append(deviation)

    order = {
        DeviationSeverity.CRITICAL: 0,
        DeviationSeverity.WARNING: 1,
        DeviationSeverity.INFO: 2,
    }
    return sorted(deviations, key=lambda entry: order[entry.severity])


def _sensor_deviation(probe: str, comparison: SensorComparison) -> Deviation | None:
    match comparison.status:
        case SensorInstallationStatus.ABSENT:
            return Deviation(
                kind=DeviationKind.SENSOR_MISSING,
                severity=DeviationSeverity.WARNING,
                object_type="sensor",
                object_ref=comparison.name,
                expected=comparison.desired_version,
                actual=None,
                remediation="sensor.deploy",
                params={"probe": probe, "sensor": comparison.name},
            )
        case SensorInstallationStatus.OUTDATED:
            return Deviation(
                kind=DeviationKind.SENSOR_OUTDATED,
                severity=DeviationSeverity.WARNING,
                object_type="sensor",
                object_ref=comparison.name,
                expected=comparison.desired_version,
                actual=comparison.installed_version,
                remediation="sensor.deploy",
                params={"probe": probe, "sensor": comparison.name},
            )
        case SensorInstallationStatus.DRIFTED:
            return Deviation(
                kind=DeviationKind.SENSOR_DRIFTED,
                severity=DeviationSeverity.WARNING,
                object_type="sensor",
                object_ref=comparison.name,
                expected=comparison.expected_sha256,
                actual=comparison.installed_sha256,
                remediation="sensor.deploy",
                params={"probe": probe, "sensor": comparison.name},
            )
        case SensorInstallationStatus.UNMANAGED:
            return Deviation(
                kind=DeviationKind.SENSOR_UNMANAGED,
                severity=DeviationSeverity.INFO,
                object_type="sensor",
                object_ref=comparison.name,
                actual=comparison.installed_version,
                # No automatic removal: adopting it into the desired state is
                # the more likely intent, and the operator decides which.
                remediation=None,
                params={"probe": probe, "sensor": comparison.name},
            )
        case _:
            return None


def build_plan(probe_username: str, deviations: list[Deviation]) -> ReconciliationPlan:
    """Turn findings into the ordered list of actions that resolves them.

    Order matters: the CA has to be right before a sensor self-test can pass,
    and the service has to run before anything is worth deploying.
    """
    actions: list[PlannedAction] = []

    for deviation in deviations:
        match deviation.kind:
            case DeviationKind.SERVICE_INACTIVE:
                actions.append(
                    PlannedAction(
                        kind="restart_service",
                        target=probe_username,
                        description_key="plan.restart_service",
                        params={"probe": probe_username},
                        restarts_service=True,
                        risk_key="plan.risk.service_interruption",
                    )
                )
            case DeviationKind.CA_MISSING | DeviationKind.CA_MISMATCHED:
                actions.append(
                    PlannedAction(
                        kind="install_ca",
                        target=probe_username,
                        description_key="plan.install_ca",
                        params={"probe": probe_username},
                    )
                )
            case DeviationKind.PROBE_NAME_MISMATCH:
                actions.append(
                    PlannedAction(
                        kind="configure",
                        target=probe_username,
                        description_key="plan.configure",
                        params={
                            "probe": probe_username,
                            "probe_name": deviation.expected or "",
                        },
                        restarts_service=True,
                        risk_key="plan.risk.service_interruption",
                    )
                )
            case (
                DeviationKind.SENSOR_MISSING
                | DeviationKind.SENSOR_OUTDATED
                | DeviationKind.SENSOR_DRIFTED
            ):
                actions.append(
                    PlannedAction(
                        kind="deploy_sensor",
                        target=deviation.object_ref,
                        description_key="plan.deploy_sensor",
                        params={
                            "probe": probe_username,
                            "sensor": deviation.object_ref,
                            "version": deviation.expected or "",
                        },
                    )
                )
            case _:
                continue

    priority = {
        "restart_service": 0,
        "install_ca": 1,
        "configure": 2,
        "deploy_sensor": 3,
    }
    actions.sort(key=lambda action: priority.get(action.kind, 99))

    return ReconciliationPlan(
        probe_username=probe_username,
        deviations=tuple(deviations),
        actions=tuple(actions),
    )
