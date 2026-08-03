"""Write the audit trail.

Every mutating action records one event. The record is written in the same
transaction as the change it describes, so an audit entry can never survive an
action that was rolled back - and an action can never happen unrecorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_correlation_id
from app.core.redaction import redact
from app.domain.enums import AuditResult
from app.persistence.models.audit import AuditEvent
from app.services.auth import Principal


@dataclass(frozen=True, slots=True)
class AuditContext:
    """The parts of a request an audit entry needs, gathered once."""

    principal: Principal | None
    source_ip: str | None


class AuditWriter:
    def __init__(self, session: AsyncSession, context: AuditContext) -> None:
        self._db = session
        self._context = context

    def record(
        self,
        *,
        action: str,
        object_type: str,
        object_id: str | None = None,
        object_label: str | None = None,
        result: AuditResult = AuditResult.SUCCESS,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        error_code: str | None = None,
        job_id: str | None = None,
        comment: str | None = None,
        actor_id: str | None = None,
        actor_name: str | None = None,
    ) -> AuditEvent:
        # Sign-in and first-run setup have no session yet, so they name the
        # actor themselves. Everywhere else it comes from the request.
        principal = self._context.principal
        resolved_id = actor_id or (None if principal is None else principal.user_id)
        resolved_name = actor_name or (
            "anonymous" if principal is None else principal.username
        )
        event = AuditEvent(
            ts=datetime.now(UTC),
            actor_id=resolved_id,
            actor_name=resolved_name,
            source_ip=self._context.source_ip,
            action=action,
            object_type=object_type,
            object_id=object_id,
            object_label=object_label,
            # Redaction lives here rather than at every call site: one place to
            # audit, and no way to forget it.
            before_state=redact(before) if before is not None else None,
            after_state=redact(after) if after is not None else None,
            result=result,
            error_code=error_code,
            job_id=job_id,
            correlation_id=get_correlation_id(),
            comment=comment,
        )
        self._db.add(event)
        return event

    def denied(
        self, *, action: str, object_type: str, object_id: str | None = None
    ) -> None:
        """A refused attempt is worth recording; it is how misuse becomes visible."""
        self.record(
            action=action,
            object_type=object_type,
            object_id=object_id,
            result=AuditResult.DENIED,
        )
