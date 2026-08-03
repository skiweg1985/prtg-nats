"""The job registry.

One place that maps a job type to its handler and its step list. The API uses
the step list when it creates a job, the runner uses the handler when it runs
one, and neither can invent a type the other does not know.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.workers.context import JobContext
from app.workers.handlers import (
    deploy_sensor,
    probe_actions,
    probe_enrollment,
    probe_lifecycle,
    sensor_actions,
    system_actions,
)

Handler = Callable[[JobContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class JobDefinition:
    type: str
    steps: tuple[str, ...]
    handler: Handler
    # Permission the API requires before it will create this job. Kept beside
    # the handler so a new job type cannot ship without one.
    permission: str


REGISTRY: dict[str, JobDefinition] = {
    deploy_sensor.JOB_TYPE: JobDefinition(
        type=deploy_sensor.JOB_TYPE,
        steps=deploy_sensor.STEPS,
        handler=deploy_sensor.run,
        permission="sensor.deploy",
    ),
    probe_actions.INSTALL_CA_JOB_TYPE: JobDefinition(
        type=probe_actions.INSTALL_CA_JOB_TYPE,
        steps=probe_actions.INSTALL_CA_STEPS,
        handler=probe_actions.install_ca,
        permission="probe.update",
    ),
    probe_actions.VALIDATE_JOB_TYPE: JobDefinition(
        type=probe_actions.VALIDATE_JOB_TYPE,
        steps=probe_actions.VALIDATE_STEPS,
        handler=probe_actions.validate,
        permission="probe.read",
    ),
    probe_actions.HELPER_UPDATE_JOB_TYPE: JobDefinition(
        type=probe_actions.HELPER_UPDATE_JOB_TYPE,
        steps=probe_actions.HELPER_UPDATE_STEPS,
        handler=probe_actions.helper_update,
        permission="probe.update",
    ),
    probe_lifecycle.CONFIGURE_JOB_TYPE: JobDefinition(
        type=probe_lifecycle.CONFIGURE_JOB_TYPE,
        steps=probe_lifecycle.CONFIGURE_STEPS,
        handler=probe_lifecycle.configure,
        permission="probe.update",
    ),
    probe_lifecycle.RECONCILE_JOB_TYPE: JobDefinition(
        type=probe_lifecycle.RECONCILE_JOB_TYPE,
        steps=probe_lifecycle.RECONCILE_STEPS,
        handler=probe_lifecycle.reconcile,
        permission="probe.reconcile",
    ),
    probe_lifecycle.UNENROLL_JOB_TYPE: JobDefinition(
        type=probe_lifecycle.UNENROLL_JOB_TYPE,
        steps=probe_lifecycle.UNENROLL_STEPS,
        handler=probe_lifecycle.unenroll,
        permission="probe.delete",
    ),
    probe_lifecycle.ROTATE_JOB_TYPE: JobDefinition(
        type=probe_lifecycle.ROTATE_JOB_TYPE,
        steps=probe_lifecycle.ROTATE_STEPS,
        handler=probe_lifecycle.rotate_credential,
        permission="credential.rotate",
    ),
    sensor_actions.REMOVE_JOB_TYPE: JobDefinition(
        type=sensor_actions.REMOVE_JOB_TYPE,
        steps=sensor_actions.REMOVE_STEPS,
        handler=sensor_actions.remove,
        permission="sensor.remove",
    ),
    sensor_actions.PROFILES_JOB_TYPE: JobDefinition(
        type=sensor_actions.PROFILES_JOB_TYPE,
        steps=sensor_actions.PROFILES_STEPS,
        handler=sensor_actions.deploy_profiles,
        permission="sensor.deploy",
    ),
    system_actions.SETUP_JOB_TYPE: JobDefinition(
        type=system_actions.SETUP_JOB_TYPE,
        steps=system_actions.SETUP_STEPS,
        handler=system_actions.setup_runtime,
        permission="system.settings",
    ),
    system_actions.RENEW_CERTIFICATE_JOB_TYPE: JobDefinition(
        type=system_actions.RENEW_CERTIFICATE_JOB_TYPE,
        steps=system_actions.RENEW_CERTIFICATE_STEPS,
        handler=system_actions.renew_certificate,
        permission="certificate.renew",
    ),
    system_actions.BACKUP_JOB_TYPE: JobDefinition(
        type=system_actions.BACKUP_JOB_TYPE,
        steps=system_actions.BACKUP_STEPS,
        handler=system_actions.backup,
        permission="system.restart",
    ),
    probe_enrollment.ENROLL_JOB_TYPE: JobDefinition(
        type=probe_enrollment.ENROLL_JOB_TYPE,
        steps=probe_enrollment.ENROLL_STEPS,
        handler=probe_enrollment.enroll,
        permission="probe.create",
    ),
    system_actions.EXPORT_JOB_TYPE: JobDefinition(
        type=system_actions.EXPORT_JOB_TYPE,
        steps=system_actions.EXPORT_STEPS,
        handler=system_actions.export_runtime,
        permission="system.restart",
    ),
    system_actions.VERIFY_JOB_TYPE: JobDefinition(
        type=system_actions.VERIFY_JOB_TYPE,
        steps=system_actions.VERIFY_STEPS,
        handler=system_actions.verify,
        permission="system.read",
    ),
    system_actions.RESTART_JOB_TYPE: JobDefinition(
        type=system_actions.RESTART_JOB_TYPE,
        steps=system_actions.RESTART_STEPS,
        handler=system_actions.restart_nats,
        permission="system.restart",
    ),
}


def get_definition(job_type: str) -> JobDefinition | None:
    return REGISTRY.get(job_type)


__all__ = ["REGISTRY", "Handler", "JobDefinition", "get_definition"]
