"""The permission vocabulary and the three built-in roles.

Permissions are fine-grained; roles are only bundles of them. That keeps the
door open for a custom role later without touching a single route.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    PROBE_READ = "probe.read"
    PROBE_CREATE = "probe.create"
    PROBE_UPDATE = "probe.update"
    PROBE_DELETE = "probe.delete"
    PROBE_RECONCILE = "probe.reconcile"

    SENSOR_READ = "sensor.read"
    SENSOR_DEPLOY = "sensor.deploy"
    SENSOR_REMOVE = "sensor.remove"
    SENSOR_CONFIGURE = "sensor.configure"

    DEPLOYMENT_READ = "deployment.read"
    DEPLOYMENT_CREATE = "deployment.create"

    JOB_READ = "job.read"
    JOB_RETRY = "job.retry"
    JOB_CANCEL = "job.cancel"

    CREDENTIAL_READ = "credential.read"
    CREDENTIAL_ROTATE = "credential.rotate"

    CERTIFICATE_READ = "certificate.read"
    CERTIFICATE_RENEW = "certificate.renew"

    IPERF_READ = "iperf.read"
    IPERF_MANAGE = "iperf.manage"

    # Availability monitoring. Reading is separate from managing because the
    # dashboard is meant for people who never touch a probe - a shop manager
    # asking whether the till printer is on gets the viewer role and nothing
    # else.
    WATCH_READ = "watch.read"
    WATCH_MANAGE = "watch.manage"

    OVERLAY_READ = "overlay.read"
    OVERLAY_MANAGE = "overlay.manage"
    # Turning the overlay on for this installation. Administrator only, and
    # not part of OPERATOR_PERMISSIONS, for the same reason system.update is
    # not: whoever presses it decides that a container with network-admin
    # rights runs in this host's network namespace.
    OVERLAY_ENABLE = "overlay.enable"

    SYSTEM_READ = "system.read"
    SYSTEM_RESTART = "system.restart"
    SYSTEM_SETTINGS = "system.settings"
    # Replacing the software this platform is made of. Administrator only, and
    # not part of OPERATOR_PERMISSIONS: whoever can trigger an update decides
    # which code runs as root on this host, because the updater holds the
    # Docker socket. docs/security/threat-model.md says so plainly.
    SYSTEM_UPDATE = "system.update"

    AUDIT_READ = "audit.read"
    USER_MANAGE = "user.manage"


class RoleName(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMINISTRATOR = "administrator"


READ_PERMISSIONS: frozenset[Permission] = frozenset(
    permission for permission in Permission if permission.value.endswith(".read")
)

OPERATOR_PERMISSIONS: frozenset[Permission] = READ_PERMISSIONS | {
    Permission.PROBE_UPDATE,
    Permission.PROBE_RECONCILE,
    Permission.SENSOR_DEPLOY,
    Permission.SENSOR_REMOVE,
    Permission.SENSOR_CONFIGURE,
    Permission.DEPLOYMENT_CREATE,
    Permission.JOB_RETRY,
    Permission.JOB_CANCEL,
    Permission.IPERF_MANAGE,
    # Adding a printer to the watch list is operating the fleet.
    Permission.WATCH_MANAGE,
    # Moving a probe between the tunnel and the direct path is operating the
    # fleet, not changing what this installation is. Enabling the overlay in
    # the first place is not here: that writes .env and starts a container
    # with NET_ADMIN, and it happens on the host.
    Permission.OVERLAY_MANAGE,
}

ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.VIEWER: READ_PERMISSIONS,
    RoleName.OPERATOR: OPERATOR_PERMISSIONS,
    RoleName.ADMINISTRATOR: frozenset(Permission),
}


def permissions_for_roles(roles: set[str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        try:
            granted |= ROLE_PERMISSIONS[RoleName(role)]
        except ValueError:
            # An unknown role grants nothing rather than everything.
            continue
    return frozenset(granted)
