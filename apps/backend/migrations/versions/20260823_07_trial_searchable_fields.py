"""Add extracted current searchable fields to trial records.

Revision ID: 20260823_07
Revises: 20260823_06
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_07"
down_revision = "20260823_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store deterministic current fields while preserving raw source snapshots."""
    op.add_column("trials", sa.Column("title", sa.Text(), nullable=True))
    op.add_column(
        "trials",
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "trials",
        sa.Column(
            "interventions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("trials", sa.Column("status", sa.String(length=64), nullable=True))
    op.add_column(
        "trials",
        sa.Column(
            "phases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("trials", sa.Column("eligibility_text", sa.Text(), nullable=True))
    op.add_column("trials", sa.Column("minimum_age", sa.String(length=64), nullable=True))
    op.add_column("trials", sa.Column("maximum_age", sa.String(length=64), nullable=True))
    op.add_column("trials", sa.Column("sex", sa.String(length=32), nullable=True))
    op.add_column(
        "trials",
        sa.Column(
            "locations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove the extracted mutable trial projection fields."""
    op.drop_column("trials", "locations")
    op.drop_column("trials", "sex")
    op.drop_column("trials", "maximum_age")
    op.drop_column("trials", "minimum_age")
    op.drop_column("trials", "eligibility_text")
    op.drop_column("trials", "phases")
    op.drop_column("trials", "status")
    op.drop_column("trials", "interventions")
    op.drop_column("trials", "conditions")
    op.drop_column("trials", "title")
