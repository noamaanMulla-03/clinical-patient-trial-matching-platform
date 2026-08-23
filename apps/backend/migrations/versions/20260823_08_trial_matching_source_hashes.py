"""Record a deterministic matching-relevant source hash for trial snapshots.

Revision ID: 20260823_08
Revises: 20260823_07
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260823_08"
down_revision = "20260823_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add matching hashes without weakening immutable raw-source history."""
    op.add_column(
        "trial_versions",
        sa.Column("matching_source_hash", sa.String(length=64), nullable=True),
    )
    # Historical rows lack a recorded extracted projection. The full source hash is
    # deliberately more sensitive, so it may trigger extra work but can never skip
    # work after a matching-relevant historical source change.
    op.execute(
        "UPDATE trial_versions "
        "SET matching_source_hash = source_hash "
        "WHERE matching_source_hash IS NULL"
    )
    op.alter_column("trial_versions", "matching_source_hash", nullable=False)
    op.add_column(
        "trials",
        sa.Column("matching_source_hash", sa.String(length=64), nullable=True),
    )
    op.execute(
        """
        UPDATE trials AS trial
        SET matching_source_hash = latest_version.matching_source_hash
        FROM (
            SELECT DISTINCT ON (nct_id) nct_id, matching_source_hash
            FROM trial_versions
            ORDER BY nct_id, ingested_at DESC, id DESC
        ) AS latest_version
        WHERE trial.nct_id = latest_version.nct_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM trials WHERE matching_source_hash IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot backfill trials.matching_source_hash without a source version';
            END IF;
        END;
        $$;
        """
    )
    op.alter_column("trials", "matching_source_hash", nullable=False)


def downgrade() -> None:
    """Remove matching-relevant source hashes."""
    op.drop_column("trials", "matching_source_hash")
    op.drop_column("trial_versions", "matching_source_hash")
