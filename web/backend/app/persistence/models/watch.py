"""Availability monitoring: what to watch, and what it did.

The tables here answer one question per device - was it reachable - and they
answer it without a time series database. ADR 0011 has the reasoning: a
measurement that agrees with the open interval extends it, one that disagrees
closes it and opens the next. What the interface reads is therefore the
history itself, not an aggregate over samples that were thrown away.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import WatchCheckMethod, WatchState
from app.persistence.base import (
    Base,
    EnumString,
    IdMixin,
    TimestampMixin,
    UtcDateTime,
)
from app.persistence.models.inventory import ProbeRecord


class WatchDevice(Base, IdMixin, TimestampMixin):
    """One thing that is supposed to be switched on.

    Assigned to exactly one probe: the probe is the vantage point, and a
    printer in one branch office is not reachable from another. Whoever moves
    a device between sites moves the assignment with it, and the intervals
    stay where they are - the history belongs to the device, not to the probe
    that happened to measure it.
    """

    __tablename__ = "watch_device"
    __table_args__ = (
        UniqueConstraint("probe_id", "address", name="uq_watch_device_probe_address"),
        Index("ix_watch_device_enabled", "enabled"),
    )

    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Hostname or IP. Resolved on the probe, not here - a name that only the
    # branch office's DNS knows is the normal case, not the exception.
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[WatchCheckMethod] = mapped_column(
        EnumString(WatchCheckMethod, 16), nullable=False, default=WatchCheckMethod.ICMP
    )
    # Only read for the TCP method; ignored otherwise rather than rejected, so
    # switching a device from TCP to ICMP and back does not lose the port.
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    probe_id: Mapped[str] = mapped_column(
        ForeignKey("probe_record.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # {"team": "support", "site": "hamburg"}. Key/value rather than a flat tag
    # list: the dashboard filters on "every device of team support", and a
    # list would make that a substring match waiting to go wrong.
    labels: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # How many consecutive failures before the device counts as down. A card
    # terminal that drops one echo request is not an outage worth waking
    # anybody for; three in a row is.
    failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    probe: Mapped[ProbeRecord] = relationship()
    observation: Mapped[WatchObservation | None] = relationship(
        back_populates="device", cascade="all, delete-orphan", uselist=False
    )


class WatchObservation(Base, IdMixin, TimestampMixin):
    """The last measurement, kept flat so the dashboard is one query.

    Derived from the reports and therefore a cache - but a cache with a
    timestamp, so a value that stopped being updated is visibly stale instead
    of quietly presented as current.
    """

    __tablename__ = "watch_observation"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("watch_device.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    state: Mapped[WatchState] = mapped_column(
        EnumString(WatchState, 16), nullable=False, default=WatchState.UNKNOWN
    )
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Consecutive failures so far. The state only flips once this reaches the
    # device's threshold, which is what keeps a single lost packet out of the
    # history.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # The address the probe actually reached, when it resolved a name. Answers
    # "it says down, but down where" without a second look.
    resolved_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    device: Mapped[WatchDevice] = relationship(back_populates="observation")


class WatchStateInterval(Base, IdMixin):
    """One uninterrupted stretch of one state.

    ``ended_at`` null means still going. That is the row the ingest extends,
    and there is at most one per device - the partial index below is what
    makes that a database rule rather than a promise.
    """

    __tablename__ = "watch_state_interval"
    __table_args__ = (
        Index("ix_watch_state_interval_device_started", "device_id", "started_at"),
        # At most one open interval per device, as a database rule rather
        # than as a promise the ingest keeps. A partial unique index also
        # answers "what is open right now" without scanning the history.
        Index(
            "ix_watch_state_interval_open",
            "device_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    device_id: Mapped[str] = mapped_column(
        ForeignKey("watch_device.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[WatchState] = mapped_column(
        EnumString(WatchState, 16), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # How much measuring went into this stretch. A four-hour "up" backed by
    # two samples is not the same statement as one backed by 240, and only
    # these two numbers can tell the difference afterwards.
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Why the stretch ended the way it did - "probe went silent" for an
    # interval the reaper closed, otherwise the last error the probe reported.
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WatchLatencyBucket(Base, IdMixin):
    """Round-trip time, summarised per device and five minutes.

    Coarse on purpose. It exists to show a device answering slower than it
    used to, not to plot a line - see ADR 0011 for why the samples themselves
    are not kept.
    """

    __tablename__ = "watch_latency_bucket"
    __table_args__ = (
        UniqueConstraint("device_id", "bucket_start", name="uq_watch_latency_bucket"),
        Index("ix_watch_latency_bucket_start", "bucket_start"),
    )

    device_id: Mapped[str] = mapped_column(
        ForeignKey("watch_device.id", ondelete="CASCADE"), nullable=False
    )
    bucket_start: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_ms: Mapped[float] = mapped_column(Float, nullable=False)
    max_ms: Mapped[float] = mapped_column(Float, nullable=False)
    # Kept as a sum rather than an average so a later measurement can be
    # folded in without the rounding error of averaging averages.
    total_ms: Mapped[float] = mapped_column(Float, nullable=False)
