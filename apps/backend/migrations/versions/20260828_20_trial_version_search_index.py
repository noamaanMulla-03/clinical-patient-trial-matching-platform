"""Index immutable trial source text for as-of lexical retrieval.

Revision ID: 20260828_20
Revises: 20260828_19
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260828_20"
down_revision = "20260828_19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index immutable ClinicalTrials.gov text without copying raw source data."""
    op.execute(
        "CREATE INDEX ix_trial_versions_raw_study_tsvector "
        "ON trial_versions USING GIN "
        "(jsonb_to_tsvector('simple'::regconfig, raw_study, '[\"string\"]'::jsonb))"
    )


def downgrade() -> None:
    """Remove the immutable-source lexical index."""
    op.execute("DROP INDEX ix_trial_versions_raw_study_tsvector")
