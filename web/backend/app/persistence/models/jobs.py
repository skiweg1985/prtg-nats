"""Jobs, their steps, their event stream and the locks they hold.

A job is the unit an operator watches. Everything that can take a while or can
fail becomes one, so the interface never has to show a spinner without a name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import JobStatus, JobStepStatus, LogLevel
from app.persistence.base import (
    Base,
    EnumString,
    IdMixin,
    TimestampMixin,
    UtcDateTime,
)


class Job(Base, IdMixin, TimestampMixin):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_status_created", "status", "created_at"),
        Index("ix_job_type_created", "type", "created_at"),
    )

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        EnumString(JobStatus), nullable=False, default=JobStatus.QUEUED
    )

    # What the job acts on, for the "Jobs" column of a probe detail page.
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who and why. trigger is "user", "schedule" or "system".
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    requested_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_user.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Input for the handler. Never holds a secret - see JobRunner, which passes
    # transient credentials in memory only.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Why it is still queued, or why it failed. Both are translation keys.
    blocked_reason_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    blocked_by_job_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # A retry points back at the job it repeats, so the history stays readable.
    retry_of_job_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStep.position",
        lazy="selectin",
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.sequence"
    )

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(self.started_at.tzinfo)
        return (end - self.started_at).total_seconds()


class JobStep(Base, IdMixin):
    __tablename__ = "job_step"
    __table_args__ = (UniqueConstraint("job_id", "position"),)

    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Machine name; the interface translates "steps.<name>".
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStepStatus] = mapped_column(
        EnumString(JobStepStatus, 16), nullable=False, default=JobStepStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # Set when a step covers several targets, e.g. one probe out of five.
    target_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    job: Mapped[Job] = relationship(back_populates="steps")


class JobEvent(Base, IdMixin):
    """One line of the live log.

    ``code`` plus ``params`` is the translatable part; ``raw`` is the technical
    output an administrator wants verbatim and which is never translated.
    """

    __tablename__ = "job_event"
    __table_args__ = (Index("ix_job_event_job_sequence", "job_id", "sequence"),)

    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    level: Mapped[LogLevel] = mapped_column(
        EnumString(LogLevel, 16), nullable=False, default=LogLevel.INFO
    )
    step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(back_populates="events")


class ResourceLock(Base, IdMixin):
    """Serialises conflicting work on the same object.

    The unique constraint is the whole mechanism: acquiring is an INSERT, and a
    second job trying the same pair simply loses the race and stays queued.
    """

    __tablename__ = "resource_lock"
    __table_args__ = (UniqueConstraint("resource_type", "resource_id"),)

    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    acquired_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # A crashed worker must not lock an object forever; the reaper uses this.
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
