"""Add durable cooperative-cancellation requests for match runs.

Revision ID: 20260823_14
Revises: 20260823_13
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_14"
down_revision = "20260823_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store one cancellation request per run outside immutable run input fields."""
    op.create_table(
        "match_run_cancellations",
        sa.Column("match_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["match_run_id"], ["match_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("match_run_id"),
    )


def downgrade() -> None:
    """Remove durable cancellation requests."""
    op.drop_table("match_run_cancellations")
