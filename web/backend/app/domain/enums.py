"""Status vocabularies shared by domain, persistence and API.

Kept deliberately small. Every value becomes a badge in the interface, and a
badge an operator has to think about is a badge that failed.
"""

from __future__ import annotations

from enum import StrEnum


class ProbeStatus(StrEnum):
    PENDING = "pending"  # inventory exists, never reached
    ENROLLED = "enrolled"  # reachable, but not yet fully configured
    HEALTHY = "healthy"  # service active, CA matches, connected to NATS
    DEGRADED = "degraded"  # reachable, something is off
    UNREACHABLE = "unreachable"  # management channel does not answer
    RETIRED = "retired"  # deliberately removed from service


class SensorInstallationStatus(StrEnum):
    ABSENT = "absent"  # wanted, not installed
    CURRENT = "current"  # installed, version matches
    OUTDATED = "outdated"  # installed, newer version available
    DRIFTED = "drifted"  # version matches, file checksum does not
    FAILED = "failed"  # last deployment failed
    UNMANAGED = "unmanaged"  # on the probe, not in the desired state


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    # Still going, but not in this process any more. A stack update replaces
    # the container it was started from, so the work carries on in a container
    # that outlives it while the job waits here to be told how it went.
    #
    # It exists as a status rather than as a flag because of what the status
    # already controls: the runner's startup recovery ends every job that says
    # "running", on the correct assumption that nothing can be running while
    # it starts. A detached job is the one thing that can, and giving it its
    # own word keeps that routine honest instead of carving an exception into
    # it. Not terminal - the outcome is still to come.
    DETACHED = "detached"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_SUCCESSFUL = "partially_successful"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_JOB_STATUSES


_TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCESSFUL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.PARTIALLY_SUCCESSFUL,
    }
)


class JobStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CertificateKind(StrEnum):
    CA = "ca"
    SERVER = "server"


class CertificateStatus(StrEnum):
    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    MISMATCHED = "mismatched"  # certificate and key do not belong together
    MISSING = "missing"


class IperfEndpointStatus(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    CREDENTIALS_STALE = "credentials_stale"
    UNKNOWN = "unknown"


class NatsConnectionState(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"  # the monitoring endpoint did not answer


class CaState(StrEnum):
    OK = "ok"
    MISSING = "missing"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class ServiceState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class DeviationKind(StrEnum):
    SENSOR_MISSING = "sensor_missing"
    SENSOR_OUTDATED = "sensor_outdated"
    SENSOR_DRIFTED = "sensor_drifted"
    SENSOR_UNMANAGED = "sensor_unmanaged"
    PROFILE_MISSING = "profile_missing"
    CA_MISSING = "ca_missing"
    CA_MISMATCHED = "ca_mismatched"
    SERVICE_INACTIVE = "service_inactive"
    PROBE_NAME_MISMATCH = "probe_name_mismatch"


class DeviationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
