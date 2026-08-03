"""The audit trail.

Append-only by contract and, on SQLite and PostgreSQL alike, by trigger. An
audit record that can be edited is a record nobody has to believe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DDL, JSON, Index, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AuditResult
from app.persistence.base import Base, EnumString, IdMixin, UtcDateTime


class AuditEvent(Base, IdMixin):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_ts", "ts"),
        Index("ix_audit_event_object", "object_type", "object_id"),
        Index("ix_audit_event_actor", "actor_name"),
    )

    ts: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    actor_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Dotted verb matching the permission that guarded it, e.g. "sensor.deploy".
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Both run through redact() before they get here. A test enforces it.
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    result: Mapped[AuditResult] = mapped_column(
        EnumString(AuditResult, 16), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


# Immutability is enforced in the database, not only in the repository: a future
# maintenance script reaching for the table directly must fail too.
_BLOCK_UPDATE = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER IF NOT EXISTS audit_event_no_update
    BEFORE UPDATE ON audit_event
    BEGIN
        SELECT RAISE(ABORT, 'audit_event is append-only');
    END;
    """
)

_BLOCK_DELETE = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER IF NOT EXISTS audit_event_no_delete
    BEFORE DELETE ON audit_event
    BEGIN
        SELECT RAISE(ABORT, 'audit_event is append-only');
    END;
    """
)

event.listen(
    AuditEvent.__table__, "after_create", _BLOCK_UPDATE.execute_if(dialect="sqlite")
)
event.listen(
    AuditEvent.__table__, "after_create", _BLOCK_DELETE.execute_if(dialect="sqlite")
)
