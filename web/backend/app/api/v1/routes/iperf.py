"""iperf3 measurement endpoints.

Listing them is here; setting one up is in the enrolment routes, next to the
probes', because it is the same ceremony with a different script. ``./prtg-nats
iperf-server install`` still works and writes the same files - an endpoint set
up from the browser is one the command line can deploy, show and revoke without
knowing where it came from.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps.common import RuntimeDep, require_permission
from app.api.schemas.system import IperfEndpointOut
from app.core.permissions import Permission

router = APIRouter(prefix="/iperf-endpoints", tags=["infrastructure"])


@router.get("", response_model=list[IperfEndpointOut])
async def list_endpoints(
    runtime: RuntimeDep,
    _: Annotated[object, Depends(require_permission(Permission.IPERF_READ))],
) -> list[IperfEndpointOut]:
    endpoints = runtime.list_iperf_endpoints()

    # Which probes hold credentials for each endpoint. Cheap to derive here and
    # the only place the answer exists, since the probes record it themselves.
    deployed: dict[str, list[str]] = {}
    for probe in runtime.read_all_probes():
        for name in probe.known_iperf_endpoints:
            deployed.setdefault(name, []).append(probe.nats_username)

    return [
        IperfEndpointOut(
            name=endpoint.name,
            host=endpoint.host,
            port=endpoint.port,
            username=endpoint.username,
            kind=endpoint.kind,
            updated_at=endpoint.updated_at,
            has_public_key=endpoint.has_public_key,
            managed=endpoint.managed,
            deployed_to=sorted(deployed.get(endpoint.name, [])),
        )
        for endpoint in endpoints
    ]
