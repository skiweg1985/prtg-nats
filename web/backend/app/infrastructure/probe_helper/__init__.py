from app.infrastructure.probe_helper.client import (
    MANAGEMENT_USER,
    HelperTransport,
    ProbeConnection,
    ProbeHelperClient,
    SshHelperTransport,
    refusal_error,
)
from app.infrastructure.probe_helper.protocol import (
    CURRENT_HELPER_VERSION,
    MINIMUM_HELPER_VERSION,
    UNSUPPORTED_REQUEST_MESSAGE,
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
    "CURRENT_HELPER_VERSION",
    "MANAGEMENT_USER",
    "MINIMUM_HELPER_VERSION",
    "SENSOR_SLOTS",
    "UNSUPPORTED_REQUEST_MESSAGE",
    "HelperCommand",
    "HelperRequest",
    "HelperResponse",
    "HelperTransport",
    "ProbeConnection",
    "ProbeHelperClient",
    "SshHelperTransport",
    "normalise_optional",
    "parse_response",
    "refusal_error",
]
