"""The audit trail, read-only by construction."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps.common import DbSession, require_permission
from app.api.schemas.system import AuditEventOut
from app.core.permissions import Permission
from app.domain.enums import AuditResult
from app.persistence.models.audit import AuditEvent

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
async def list_audit_events(
    db: DbSession,
    _: Annotated[object, Depends(require_permission(Permission.AUDIT_READ))],
    actor: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    object_type: Annotated[str | None, Query()] = None,
    object_id: Annotated[str | None, Query()] = None,
    result: Annotated[AuditResult | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[str | None, Query()] = None,
) -> list[AuditEventOut]:
    """Filterable history.

    There is no write endpoint and no delete endpoint. Records are written by
    the services that perform the actions, and a database trigger refuses any
    UPDATE or DELETE - an audit trail somebody can tidy up is not one.
    """
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if actor:
        query = query.where(AuditEvent.actor_name == actor)
    if action:
        query = query.where(AuditEvent.action == action)
    if object_type:
        query = query.where(AuditEvent.object_type == object_type)
    if object_id:
        query = query.where(AuditEvent.object_id == object_id)
    if result:
        query = query.where(AuditEvent.result == result)
    if since:
        query = query.where(AuditEvent.ts >= since)
    if before:
        query = query.where(AuditEvent.id < before)

    events = await db.scalars(query)
    return [AuditEventOut.model_validate(event) for event in events]
