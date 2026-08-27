"""What the platform knows about its own version, and about an update in flight.

Two rows with very different lifetimes, and they are here together because
both answer "which version of this software is where".

``StackVersion`` is a cache. It holds what the last look at the checkout and
the repository found, so the interface can answer immediately instead of
starting a container on every page load.

``StackUpdate`` is the handover record. An update replaces the process running
it, so the job that started it cannot finish it - the next process picks it up
from here. Without this row there would be nothing connecting a container that
outlived its caller to the job that is still waiting to hear how it went.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, IdMixin, TimestampMixin, UtcDateTime


class StackVersion(Base, IdMixin, TimestampMixin):
    """The last answer the updater gave about where this installation stands.

    A single row, replaced on every check. History would be a nice thing to
    have and a poor thing to keep: the interesting past of an installation is
    its job log, which already records every update that ran.
    """

    __tablename__ = "stack_version"

    # Which branch this installation follows.
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # What the checkout on the host is at.
    checkout_commit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Whether that checkout has uncommitted changes. An update refuses to run
    # over them, so this decides whether the button is offered at all.
    checkout_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What the branch has at its tip.
    remote_commit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Whether the repository answered at all. False with an empty error never
    # happens; false with a reason is a deploy key that no longer works, and
    # the interface has to say so rather than show "up to date".
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The commits between the two, newest first, as the updater reported them.
    commits: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class StackUpdate(Base, IdMixin, TimestampMixin):
    """One update in flight, and how to pick it up again afterwards."""

    __tablename__ = "stack_update"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The container doing the work. It carries the log and the exit code, and
    # it is deliberately not removed when it ends - the process that reads
    # both may not exist yet at that point.
    container_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Where the checkout was, so a rollback knows where to put it back.
    commit_from: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    commit_to: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # The checkout path on the host, recorded rather than looked up again: a
    # recovery has to work even if the labels have since changed.
    checkout_dir: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Unix seconds of the last log line already copied into the job, so the
    # recovery can ask the daemon for the rest instead of duplicating what the
    # operator has already read.
    log_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set once the outcome has been recorded. An unsettled row is the whole
    # signal the next process needs.
    settled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
