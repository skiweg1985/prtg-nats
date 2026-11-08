"""Every model, imported so Alembic autogenerate sees the full metadata."""

from app.persistence.base import Base
from app.persistence.models.audit import AuditEvent
from app.persistence.models.identity import (
    LoginAttempt,
    Session,
    UserRole,
    WebUser,
)
from app.persistence.models.inventory import (
    Alert,
    Deployment,
    DeploymentTarget,
    EnrollmentToken,
    ProbeDesiredState,
    ProbeManagementRoute,
    ProbeObservedState,
    ProbeRecord,
    ProbeReturnChannel,
    SavedView,
    Setting,
)
from app.persistence.models.jobs import Job, JobEvent, JobStep, ResourceLock
from app.persistence.models.updates import StackUpdate, StackVersion

__all__ = [
    "Alert",
    "AuditEvent",
    "Base",
    "Deployment",
    "DeploymentTarget",
    "EnrollmentToken",
    "Job",
    "JobEvent",
    "JobStep",
    "LoginAttempt",
    "ProbeDesiredState",
    "ProbeManagementRoute",
    "ProbeObservedState",
    "ProbeRecord",
    "ProbeReturnChannel",
    "ResourceLock",
    "SavedView",
    "Session",
    "Setting",
    "StackUpdate",
    "StackVersion",
    "UserRole",
    "WebUser",
]
