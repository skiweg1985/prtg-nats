"""State the filesystem does not hold.

runtime/ stays the source of truth for credentials, certificates and the probe
inventory - the NATS container and the shell tooling read those files directly.
What lives here is what runtime/ has no place for: what an operator *wants*,
what we last *observed*, and the deployments in between.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import AlertSeverity, JobStatus
from app.persistence.base import (
    Base,
    EnumString,
    IdMixin,
    TimestampMixin,
    UtcDateTime,
)


class ProbeRecord(Base, IdMixin, TimestampMixin):
    """The stable identity of a probe inside the web platform.

    The NATS username is the natural key everywhere in the shell tooling, so it
    stays unique here too. The ULID exists so URLs survive a rename.
    """

    __tablename__ = "probe_record"

    nats_username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    # The operator's own tick for the two steps the platform cannot take or
    # see: the access key entered in PRTG and the probe approved there. Not a
    # probe status - a probe is healthy without it, just invisible to PRTG.
    prtg_registered_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    prtg_registered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    desired_states: Mapped[list[ProbeDesiredState]] = relationship(
        back_populates="probe", cascade="all, delete-orphan"
    )
    observed_state: Mapped[ProbeObservedState | None] = relationship(
        back_populates="probe", cascade="all, delete-orphan", uselist=False
    )


class ProbeDesiredState(Base, IdMixin, TimestampMixin):
    """A versioned document describing what should be true on a probe.

    Kept as a whole document rather than normalised rows: it is written and
    compared as a unit, and keeping every version makes "what changed and who
    changed it" a query instead of an archaeology project.
    """

    __tablename__ = "probe_desired_state"
    __table_args__ = (UniqueConstraint("probe_id", "version"),)

    probe_id: Mapped[str] = mapped_column(
        ForeignKey("probe_record.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # {"sensors": [{"name": ..., "version": ..., "profiles": [...],
    #               "interfaces": [...]}],
    #  "probe_name": ..., "ca_required": true}
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_user.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    probe: Mapped[ProbeRecord] = relationship(back_populates="desired_states")


class ProbeObservedState(Base, IdMixin, TimestampMixin):
    """The last answer a probe gave, with the time it gave it.

    A cache, explicitly. Every read hands ``observed_at`` to the interface so a
    stale value is visibly stale instead of quietly wrong.
    """

    __tablename__ = "probe_observed_state"

    probe_id: Mapped[str] = mapped_column(
        ForeignKey("probe_record.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when something changed the probe but the probe could not be asked
    # about it - a job that finished against a host that was still restarting.
    # The sync worker treats it like a stale timestamp, which puts the probe in
    # the next pass instead of the one after the staleness window. Kept apart
    # from observed_at so the interface can still say truthfully when the probe
    # last answered.
    refresh_due: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Full ObservedProbeState as produced by the probe helper adapter.
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Populated when the probe did not answer, so the list can explain itself.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    probe: Mapped[ProbeRecord] = relationship(back_populates="observed_state")


class Deployment(Base, IdMixin, TimestampMixin):
    """One rollout of one sensor to one or more probes."""

    __tablename__ = "deployment"
    __table_args__ = (Index("ix_deployment_created", "created_at"),)

    sensor_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sensor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        EnumString(JobStatus), nullable=False, default=JobStatus.QUEUED
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("job.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    targets: Mapped[list[DeploymentTarget]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan", lazy="selectin"
    )


class DeploymentTarget(Base, IdMixin):
    """The outcome for one probe inside a rollout.

    Separate from the job's step list because a rollout to twelve probes has
    twelve independent outcomes, and "eleven fine, one failed" has to be
    readable at a glance.
    """

    __tablename__ = "deployment_target"
    __table_args__ = (UniqueConstraint("deployment_id", "probe_id"),)

    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    probe_id: Mapped[str] = mapped_column(String(26), nullable=False)
    probe_label: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        EnumString(JobStatus), nullable=False, default=JobStatus.QUEUED
    )
    previous_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    deployment: Mapped[Deployment] = relationship(back_populates="targets")


class Alert(Base, IdMixin, TimestampMixin):
    """A standing condition the dashboard should surface.

    Derived, not authored: the inventory sync raises and clears these. Storing
    them makes "since when" answerable and keeps the dashboard a single query.
    """

    __tablename__ = "alert"
    __table_args__ = (UniqueConstraint("kind", "object_ref"),)

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        EnumString(AlertSeverity, 16), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    object_label: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SavedView(Base, IdMixin, TimestampMixin):
    """A named filter set on a table, per user or shared."""

    __tablename__ = "saved_view"
    __table_args__ = (UniqueConstraint("owner_id", "scope", "name"),)

    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("web_user.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Setting(Base, TimestampMixin):
    """System settings as key/value, so adding one needs no migration."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EnrollmentToken(Base, IdMixin, TimestampMixin):
    """An invitation for one host to enrol itself, once, before it expires.

    Deliberately here and not in runtime/, which is otherwise the source of
    truth: a file would outlive the token's validity and quietly become a
    standing credential. A single-use secret with a lifetime is exactly the
    kind of state ADR 0002 carves out for the database.

    Like a session, only the hash is stored. A database dump must not hand out
    the right to enrol a host into the fleet.
    """

    __tablename__ = "enrollment_token"
    __table_args__ = (Index("ix_enrollment_token_expires_at", "expires_at"),)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # probe | iperf
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # What the finished host should become: NATS account, probe name, endpoint
    # name and port. Never a secret - those live in runtime/ where they belong.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Set when the operator knows the address; otherwise the callback's source
    # address is the suggestion, which is wrong exactly when the host is
    # behind NAT.
    expected_host: Mapped[str | None] = mapped_column(String(255), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # Where the host answered from, and what it told us about itself - its
    # host keys above all, which is what gets pinned.
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reported: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    job_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
