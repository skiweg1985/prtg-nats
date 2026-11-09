"""hidden sensor stream

A row of a sensor's timeline taken off the page on purpose - a network that
was configured by mistake and would otherwise stay there for a day. Interface
state per ADR 0002: the sensor log records behind it stay in the runtime
volume untouched, and the row comes back with the first run after it was
hidden.

Revision ID: f3a8c61d2b47
Revises: b9d4e2f7a813
Create Date: 2026-11-09 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a8c61d2b47"
down_revision: str | None = "b9d4e2f7a813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hidden_sensor_stream",
        sa.Column("probe_id", sa.String(length=26), nullable=False),
        sa.Column("sensor", sa.String(length=64), nullable=False),
        sa.Column("stream", sa.String(length=255), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hidden_by", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["probe_id"],
            ["probe_record.id"],
            name=op.f("fk_hidden_sensor_stream_probe_id_probe_record"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hidden_sensor_stream")),
        sa.UniqueConstraint(
            "probe_id",
            "sensor",
            "stream",
            name=op.f("uq_hidden_sensor_stream_probe_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("hidden_sensor_stream")
