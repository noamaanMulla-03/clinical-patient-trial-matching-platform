"""Preserve versioned parser provenance for each trial snapshot.

Revision ID: 20260826_17
Revises: 20260826_16
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260826_17"
down_revision = "20260826_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store one immutable deterministic parser output per public trial version."""
    op.create_table(
        "trial_parser_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column(
            "model_configuration_version", sa.String(length=128), nullable=False
        ),
        sa.Column(
            "raw_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trial_version_id"], ["trial_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_trial_parser_runs_trial_version_id",
        "trial_parser_runs",
        ["trial_version_id"],
        unique=True,
    )
    op.execute(
        """
        CREATE TRIGGER protect_trial_parser_runs_history
        BEFORE UPDATE OR DELETE ON trial_parser_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_historical_snapshot_changes();
        """
    )


def downgrade() -> None:
    """Remove the immutable parser-run table and its lifecycle guard."""
    op.execute("DROP TRIGGER protect_trial_parser_runs_history ON trial_parser_runs")
    op.drop_index("uq_trial_parser_runs_trial_version_id", "trial_parser_runs")
    op.drop_table("trial_parser_runs")
