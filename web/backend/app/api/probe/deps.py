"""Who is calling on the return channel."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.api.deps.common import RuntimeDep
from app.core.errors import NotFoundError, ProbeNotEnrolledError

# Set by the proxy from the subject of the verified client certificate, after
# it has dropped whatever the client sent under the same name. The backend
# listens on loopback only, so nothing else can put it there.
PROBE_IDENTITY_HEADER = "X-Probe-Identity"


async def get_probe_identity(
    runtime: RuntimeDep,
    identity: Annotated[str | None, Header(alias=PROBE_IDENTITY_HEADER)] = None,
) -> str:
    """The probe's NATS account (ADR 0011), if the inventory still holds it.

    Checked on every request rather than at certificate issue: a probe that
    was retired keeps a valid certificate until it expires, and the inventory
    in the runtime is what decides who belongs to this installation (ADR 0002).
    """
    if not identity:
        raise ProbeNotEnrolledError()
    try:
        runtime.read_probe(identity)
    except NotFoundError:
        raise ProbeNotEnrolledError(params={"probe": identity}) from None
    return identity


ProbeIdentityDep = Annotated[str, Depends(get_probe_identity)]
