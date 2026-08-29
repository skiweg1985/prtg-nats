"""drop unreachable columns

Two columns nothing ever wrote. probe_record.retired_at fed a RETIRED status
no code path could produce - the enum value, its badge and its translation
were unreachable with it. probe_desired_state.applied_at was written by
nobody and read by nobody. Both can only be empty, so dropping them loses
nothing; the downgrade recreates them as the nullable blanks they were.

Revision ID: e8b3f1a26c47
Revises: d4a2c9e71f3b
Create Date: 2026-08-29 17:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8b3f1a26c47"
down_revision: str | None = "d4a2c9e71f3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("probe_record", schema=None) as batch_op:
        batch_op.drop_column("retired_at")
    with op.batch_alter_table("probe_desired_state", schema=None) as batch_op:
        batch_op.drop_column("applied_at")


def downgrade() -> None:
    with op.batch_alter_table("probe_desired_state", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("probe_record", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True)
        )
