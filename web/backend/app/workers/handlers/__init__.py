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
    iperf_enrollment,
    iperf_provisioning,
    probe_actions,
    probe_enrollment,
    probe_lifecycle,
    sensor_actions,
    stack_update,
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
    # Whether the runner asks the probes this job held about the state it left
    # them in. On by default: a job that takes a probe has changed it often
    # enough that forgetting to say so is the more expensive mistake, and the
    # cost of being wrong is one round trip to a host that answered seconds
    # ago. Off only where there is nothing left to ask.
    refreshes_probes: bool = True


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
    probe_actions.REFRESH_JOB_TYPE: JobDefinition(
        type=probe_actions.REFRESH_JOB_TYPE,
        steps=probe_actions.REFRESH_STEPS,
        handler=probe_actions.refresh,
        permission="probe.read",
        # Asking a second time is what this job already did, once per probe,
        # and wrote down.
        refreshes_probes=False,
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
        # The probe is out of the inventory by the time this ends. Asking it
        # anything would mean reading an entry that is gone and putting the
        # record back that the job just removed.
        refreshes_probes=False,
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
    sensor_actions.RESERVE_INTERFACE_JOB_TYPE: JobDefinition(
        type=sensor_actions.RESERVE_INTERFACE_JOB_TYPE,
        steps=sensor_actions.RESERVE_INTERFACE_STEPS,
        handler=sensor_actions.reserve_interface,
        permission="sensor.configure",
    ),
    sensor_actions.RELEASE_INTERFACE_JOB_TYPE: JobDefinition(
        type=sensor_actions.RELEASE_INTERFACE_JOB_TYPE,
        steps=sensor_actions.RELEASE_INTERFACE_STEPS,
        handler=sensor_actions.release_interface,
        permission="sensor.configure",
    ),
    sensor_actions.PROFILES_JOB_TYPE: JobDefinition(
        type=sensor_actions.PROFILES_JOB_TYPE,
        steps=sensor_actions.PROFILES_STEPS,
        handler=sensor_actions.deploy_profiles,
        permission="sensor.deploy",
    ),
    sensor_actions.WRITE_PROFILE_JOB_TYPE: JobDefinition(
        type=sensor_actions.WRITE_PROFILE_JOB_TYPE,
        steps=sensor_actions.WRITE_PROFILE_STEPS,
        handler=sensor_actions.write_profile,
        permission="sensor.configure",
    ),
    sensor_actions.REMOVE_PROFILE_JOB_TYPE: JobDefinition(
        type=sensor_actions.REMOVE_PROFILE_JOB_TYPE,
        steps=sensor_actions.REMOVE_PROFILE_STEPS,
        handler=sensor_actions.remove_profile,
        permission="sensor.configure",
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
    iperf_enrollment.ENROLL_JOB_TYPE: JobDefinition(
        type=iperf_enrollment.ENROLL_JOB_TYPE,
        steps=iperf_enrollment.ENROLL_STEPS,
        handler=iperf_enrollment.enroll,
        permission="iperf.manage",
        # This job touches no probe. Asking the fleet about itself afterwards
        # would be a round trip per probe for a host none of them has heard
        # from yet - the credentials reach them in their own job.
        refreshes_probes=False,
    ),
    iperf_provisioning.PROVISION_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.PROVISION_JOB_TYPE,
        steps=iperf_provisioning.PROVISION_STEPS,
        handler=iperf_provisioning.provision,
        permission="iperf.manage",
        refreshes_probes=False,
    ),
    iperf_provisioning.REMOVE_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.REMOVE_JOB_TYPE,
        steps=iperf_provisioning.REMOVE_STEPS,
        handler=iperf_provisioning.remove,
        permission="iperf.manage",
        # This one does touch probes: they lose a credential profile, and the
        # sensor that used it reports differently from the next run onwards.
    ),
    iperf_provisioning.ROTATE_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.ROTATE_JOB_TYPE,
        steps=iperf_provisioning.ROTATE_STEPS,
        handler=iperf_provisioning.rotate,
        permission="iperf.manage",
    ),
    iperf_provisioning.FOREIGN_CREDENTIALS_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.FOREIGN_CREDENTIALS_JOB_TYPE,
        steps=iperf_provisioning.FOREIGN_CREDENTIALS_STEPS,
        handler=iperf_provisioning.update_foreign_credentials,
        permission="iperf.manage",
        # Probe resources include the whole known fleet to cover holders a
        # previously queued deploy adds. The handler already reports the
        # actual holders and changes no inventory state, so refreshing every
        # locked non-holder afterwards would only create avoidable traffic.
        refreshes_probes=False,
    ),
    # Deploying and revoking are what the fleet actually notices about an
    # endpoint, so they carry the permission that decides who may change what a
    # probe holds - not the one for setting an endpoint up.
    iperf_provisioning.DEPLOY_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.DEPLOY_JOB_TYPE,
        steps=iperf_provisioning.DEPLOY_STEPS,
        handler=iperf_provisioning.deploy,
        permission="sensor.deploy",
    ),
    iperf_provisioning.REVOKE_JOB_TYPE: JobDefinition(
        type=iperf_provisioning.REVOKE_JOB_TYPE,
        steps=iperf_provisioning.REVOKE_STEPS,
        handler=iperf_provisioning.revoke,
        permission="sensor.deploy",
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
    stack_update.JOB_TYPE: JobDefinition(
        type=stack_update.JOB_TYPE,
        steps=stack_update.STEPS,
        handler=stack_update.run,
        permission="system.update",
        # This job touches no probe, and the moment it ends is the worst
        # possible one to ask the whole fleet about itself: the service has
        # just come back up and would answer an SSH round trip per host while
        # it is still finding its feet.
        refreshes_probes=False,
    ),
}


def get_definition(job_type: str) -> JobDefinition | None:
    return REGISTRY.get(job_type)


__all__ = ["REGISTRY", "Handler", "JobDefinition", "get_definition"]
