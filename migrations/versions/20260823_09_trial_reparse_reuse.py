"""Link unchanged matching inputs to the source version whose work they reuse.

Revision ID: 20260823_09
Revises: 20260823_08
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_09"
down_revision = "20260823_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Mark source snapshots that require parsing versus safe derived-work reuse."""
    op.add_column(
        "trial_versions",
        sa.Column(
            "matching_reused_from_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "trial_versions",
        sa.Column("requires_reparse", sa.Boolean(), nullable=True),
    )
    # Historical snapshots did not record reusable matching inputs. Reprocessing is
    # conservative and cannot silently reuse derived work from an unknown source.
    op.execute(
        "UPDATE trial_versions SET requires_reparse = TRUE WHERE requires_reparse IS NULL"
    )
    op.alter_column("trial_versions", "requires_reparse", nullable=False)
    op.create_foreign_key(
        "fk_trial_versions_matching_reused_from_version_id",
        "trial_versions",
        "trial_versions",
        ["matching_reused_from_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_trial_versions_reparse_source",
        "trial_versions",
        "requires_reparse IS TRUE OR matching_reused_from_version_id IS NOT NULL",
    )
    op.create_index(
        "ix_trial_versions_nct_id_matching_source_hash",
        "trial_versions",
        ["nct_id", "matching_source_hash"],
        unique=False,
    )


def downgrade() -> None:
    """Remove explicit matching-derived-work reuse links."""
    op.drop_index("ix_trial_versions_nct_id_matching_source_hash", "trial_versions")
    op.drop_constraint("ck_trial_versions_reparse_source", "trial_versions")
    op.drop_constraint(
        "fk_trial_versions_matching_reused_from_version_id", "trial_versions"
    )
    op.drop_column("trial_versions", "requires_reparse")
    op.drop_column("trial_versions", "matching_reused_from_version_id")
