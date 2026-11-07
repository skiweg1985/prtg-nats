"""probe management route

The address a probe last answered management on - its overlay address or its
site address - and when. The transport has reported it to an observer since
ADR 0011; this keeps it, so the probe's page can say which route works
without anybody reading the API log. A cache with a timestamp per ADR 0002,
in a table of its own because every successful call writes it, not only the
refresh that writes the observed state.

Revision ID: a7c3e5f19d42
Revises: e8b3f1a26c47
Create Date: 2026-11-07 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e5f19d42"
down_revision: str | None = "e8b3f1a26c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "probe_management_route",
        sa.Column("probe_id", sa.String(length=26), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("over_overlay", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["probe_id"],
            ["probe_record.id"],
            name=op.f("fk_probe_management_route_probe_id_probe_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_probe_management_route")),
        sa.UniqueConstraint(
            "probe_id", name=op.f("uq_probe_management_route_probe_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("probe_management_route")
