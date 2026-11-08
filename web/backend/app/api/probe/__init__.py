"""Routes a probe calls over its return channel (ADR 0013).

Kept apart from app/api/v1 on purpose. These routes are not part of the
interface's API: they answer on the probe channel port only, behind a proxy
that requires a client certificate, and the web site never forwards
``/probe/v1``. No session and no permission applies - the probe's identity,
checked against the inventory, is the whole authorisation.
"""
