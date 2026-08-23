"""Add PostgreSQL full-text indexes for the current trial projection.

Revision ID: 20260823_12
Revises: 20260823_11
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260823_12"
down_revision = "20260823_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index textual current-trial fields without indexing immutable raw payloads."""
    op.execute(
        "CREATE INDEX ix_trials_title_fts ON trials USING gin "
        "(to_tsvector('simple', coalesce(title, '')))"
    )
    op.execute(
        "CREATE INDEX ix_trials_conditions_fts ON trials USING gin "
        "(jsonb_to_tsvector('simple', conditions, '[\"string\"]'::jsonb))"
    )
    op.execute(
        "CREATE INDEX ix_trials_interventions_fts ON trials USING gin "
        "(jsonb_to_tsvector('simple', interventions, '[\"string\"]'::jsonb))"
    )
    op.execute(
        "CREATE INDEX ix_trials_eligibility_text_fts ON trials USING gin "
        "(to_tsvector('simple', coalesce(eligibility_text, '')))"
    )


def downgrade() -> None:
    """Remove the current-trial lexical indexes."""
    op.execute("DROP INDEX ix_trials_eligibility_text_fts")
    op.execute("DROP INDEX ix_trials_interventions_fts")
    op.execute("DROP INDEX ix_trials_conditions_fts")
    op.execute("DROP INDEX ix_trials_title_fts")
