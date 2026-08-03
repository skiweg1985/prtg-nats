"""enrollment tokens

An invitation for one host to enrol itself. Lives here rather than in runtime/
because a file would outlive the token's validity and become a standing
credential; only the hash is stored, for the same reason sessions store only
theirs.

Revision ID: 707972bba671
Revises: fc92d48b6910
Create Date: 2026-08-03 09:02:20.656021
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "707972bba671"
down_revision: str | None = "fc92d48b6910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrollment_token",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("expected_host", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("reported", sa.JSON(), nullable=True),
        sa.Column("job_id", sa.String(length=26), nullable=True),
        sa.Column("created_by_id", sa.String(length=26), nullable=True),
        sa.Column("created_by_name", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollment_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_enrollment_token_token_hash")),
    )
    with op.batch_alter_table("enrollment_token", schema=None) as batch_op:
        batch_op.create_index(
            "ix_enrollment_token_expires_at", ["expires_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("enrollment_token", schema=None) as batch_op:
        batch_op.drop_index("ix_enrollment_token_expires_at")

    op.drop_table("enrollment_token")
