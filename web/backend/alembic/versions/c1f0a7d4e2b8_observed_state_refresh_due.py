"""observed state refresh due

A job that changes a probe refreshes the cached observation itself. When the
probe cannot be asked right then - a host still restarting its service, say -
the cache is marked instead of overwritten: the next sync pass asks again
rather than waiting out the staleness window, and what the interface shows in
the meantime is still the last thing the probe actually said.

Revision ID: c1f0a7d4e2b8
Revises: 707972bba671
Create Date: 2026-08-04 10:12:44.108233
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1f0a7d4e2b8"
down_revision: str | None = "707972bba671"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("probe_observed_state", schema=None) as batch_op:
        # Existing rows are as good as they ever were; nothing is due.
        batch_op.add_column(
            sa.Column(
                "refresh_due",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("probe_observed_state", schema=None) as batch_op:
        batch_op.drop_column("refresh_due")
