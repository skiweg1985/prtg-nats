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

# What Caddy writes into the header: {http.request.tls.client.subject}, the
# subject as Go prints it. The probe CA issues CN-only subjects, so that is
# exactly "CN=<identity>". Anything else did not come from a certificate this
# installation signed and is refused rather than picked apart; the name rules
# themselves are left to the inventory lookup.
_SUBJECT_PREFIX = "CN="


async def get_probe_identity(
    runtime: RuntimeDep,
    identity: Annotated[str | None, Header(alias=PROBE_IDENTITY_HEADER)] = None,
) -> str:
    """The probe's NATS account (ADR 0011), if the inventory still holds it.

    Checked on every request rather than at certificate issue: a probe that
    was retired keeps a valid certificate until it expires, and the inventory
    in the runtime is what decides who belongs to this installation (ADR 0002).
    """
    if not identity or not identity.startswith(_SUBJECT_PREFIX):
        raise ProbeNotEnrolledError()
    # A second attribute or an escaped character leaves a value no NATS
    # username can have, so read_probe refuses it without any DN parsing here.
    identity = identity.removeprefix(_SUBJECT_PREFIX)
    try:
        runtime.read_probe(identity)
    except NotFoundError:
        raise ProbeNotEnrolledError(params={"probe": identity}) from None
    return identity


ProbeIdentityDep = Annotated[str, Depends(get_probe_identity)]
