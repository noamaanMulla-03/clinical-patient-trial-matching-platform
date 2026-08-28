"""Retain explicit review reasons for parsed trial criteria.

Revision ID: 20260826_16
Revises: 20260825_15
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260826_16"
down_revision = "20260825_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add immutable parser-review reason codes without altering source text."""
    op.add_column(
        "criteria",
        sa.Column(
            "review_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Remove the parser-review metadata column."""
    op.drop_column("criteria", "review_reasons")
