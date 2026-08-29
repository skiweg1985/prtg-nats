"""prtg registration flag

The platform cannot ask PRTG whether a probe's access key was entered and the
probe approved - the two manual steps every enrolment ends with. Until now
that state existed nowhere, and a probe nobody ever registered in PRTG stood
green. These columns hold the operator's own tick: who marked the probe as
registered, and when. Nullable, because "never ticked" is the state every
existing probe is in and the honest default for new ones.

Revision ID: d4a2c9e71f3b
Revises: b17d95c5b98e
Create Date: 2026-08-29 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a2c9e71f3b"
down_revision: str | None = "b17d95c5b98e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("probe_record", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("prtg_registered_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("prtg_registered_by", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("probe_record", schema=None) as batch_op:
        batch_op.drop_column("prtg_registered_by")
        batch_op.drop_column("prtg_registered_at")
