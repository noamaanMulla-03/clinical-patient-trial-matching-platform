"""Add trial-ingestion status, safe failure details, and freshness metrics.

Revision ID: 20260823_11
Revises: 20260823_10
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_11"
down_revision = "20260823_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create one durable operational record for every trial ingestion attempt."""
    op.create_table(
        "trial_syncs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "request_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "studies_processed", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "versions_created", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "unchanged_studies", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "versions_requiring_reparse",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "versions_reusing_matching_results",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "source_records_with_update_time",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "source_records_missing_update_time",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "source_records_invalid_update_time",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("max_source_lag_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_trial_syncs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove durable trial-ingestion operational records."""
    op.drop_table("trial_syncs")
