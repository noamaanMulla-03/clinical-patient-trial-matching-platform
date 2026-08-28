"""Record safe per-run retrieval mode and coverage without clinical text.

Revision ID: 20260828_19
Revises: 20260827_18
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260828_19"
down_revision = "20260827_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add operational retrieval evidence separately from frozen input config."""
    op.execute(
        "ALTER TABLE match_runs ADD COLUMN retrieval_execution JSONB "
        "NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    """Remove operational retrieval evidence."""
    op.execute("ALTER TABLE match_runs DROP COLUMN retrieval_execution")
