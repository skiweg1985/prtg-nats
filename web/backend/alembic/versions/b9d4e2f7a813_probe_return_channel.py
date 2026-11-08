"""probe return channel

What a probe's return channel last reported: when it last delivered, how full
its buffer was and the gaps it announced (ADR 0013, issue #176). A cache with
a timestamp per ADR 0002, in a table of its own because every delivery writes
it, the way every management answer writes the management route.

Revision ID: b9d4e2f7a813
Revises: a7c3e5f19d42
Create Date: 2026-11-08 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9d4e2f7a813"
down_revision: str | None = "a7c3e5f19d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "probe_return_channel",
        sa.Column("probe_id", sa.String(length=26), nullable=False),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("buffer_bytes", sa.Integer(), nullable=True),
        sa.Column("buffer_limit", sa.Integer(), nullable=True),
        sa.Column("gap_count", sa.Integer(), nullable=False),
        sa.Column("last_gap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["probe_id"],
            ["probe_record.id"],
            name=op.f("fk_probe_return_channel_probe_id_probe_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_probe_return_channel")),
        sa.UniqueConstraint("probe_id", name=op.f("uq_probe_return_channel_probe_id")),
    )


def downgrade() -> None:
    op.drop_table("probe_return_channel")
