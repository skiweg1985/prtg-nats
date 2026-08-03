from app.infrastructure.probe_helper.client import (
    MANAGEMENT_USER,
    HelperTransport,
    ProbeConnection,
    ProbeHelperClient,
    SshHelperTransport,
)
from app.infrastructure.probe_helper.protocol import (
    HelperCommand,
    HelperRequest,
    HelperResponse,
    normalise_optional,
    parse_response,
)

# The four file slots the probe helper knows, in the order a deployment sends
# them. "version" goes last: it is what sensor-list reports back, so it must
# only appear once everything it describes is in place.
SENSOR_SLOTS: tuple[str, ...] = ("script", "wrapper", "requirements", "version")

__all__ = [
    "MANAGEMENT_USER",
    "SENSOR_SLOTS",
    "HelperCommand",
    "HelperRequest",
    "HelperResponse",
    "HelperTransport",
    "ProbeConnection",
    "ProbeHelperClient",
    "SshHelperTransport",
    "normalise_optional",
    "parse_response",
]
