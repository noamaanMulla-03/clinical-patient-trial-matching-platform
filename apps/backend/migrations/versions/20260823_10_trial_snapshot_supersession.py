"""Retain and explicitly link superseded immutable trial source snapshots.

Revision ID: 20260823_10
Revises: 20260823_09
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_10"
down_revision = "20260823_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an append-only source-version supersession chain."""
    op.add_column(
        "trial_versions",
        sa.Column(
            "superseded_by_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "trial_versions",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Replace the generic fully-immutable guard only for trial versions. It still
    # forbids every source-field mutation, but permits one paired lifecycle update.
    op.execute("DROP TRIGGER protect_trial_versions_history ON trial_versions")
    op.execute(
        """
        CREATE FUNCTION prevent_trial_version_source_changes()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.nct_id IS DISTINCT FROM OLD.nct_id
               OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
               OR NEW.matching_source_hash IS DISTINCT FROM OLD.matching_source_hash
               OR NEW.matching_reused_from_version_id
                  IS DISTINCT FROM OLD.matching_reused_from_version_id
               OR NEW.requires_reparse IS DISTINCT FROM OLD.requires_reparse
               OR NEW.raw_study IS DISTINCT FROM OLD.raw_study
               OR NEW.source_updated_at IS DISTINCT FROM OLD.source_updated_at
               OR NEW.retrieved_at IS DISTINCT FROM OLD.retrieved_at
               OR NEW.ingested_at IS DISTINCT FROM OLD.ingested_at
               OR OLD.superseded_at IS NOT NULL
               OR OLD.superseded_by_version_id IS NOT NULL
               OR NEW.superseded_at IS NULL
               OR NEW.superseded_by_version_id IS NULL THEN
                RAISE EXCEPTION 'trial_versions source snapshots are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_trial_versions_history
        BEFORE UPDATE OR DELETE ON trial_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_trial_version_source_changes();
        """
    )
    op.create_foreign_key(
        "fk_trial_versions_superseded_by_version_id",
        "trial_versions",
        "trial_versions",
        ["superseded_by_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_trial_versions_supersession_pair",
        "trial_versions",
        "(superseded_at IS NULL) = (superseded_by_version_id IS NULL)",
    )
    op.create_index(
        "ix_trial_versions_nct_id_superseded_at",
        "trial_versions",
        ["nct_id", "superseded_at"],
        unique=False,
    )
    # Existing rows are ordered by their immutable ingestion time. Every earlier
    # source snapshot gains its successor while the newest remains current.
    op.execute(
        """
        WITH ordered_versions AS (
            SELECT
                id,
                LEAD(id) OVER (
                    PARTITION BY nct_id ORDER BY ingested_at, id
                ) AS successor_id,
                LEAD(retrieved_at) OVER (
                    PARTITION BY nct_id ORDER BY ingested_at, id
                ) AS successor_retrieved_at
            FROM trial_versions
        )
        UPDATE trial_versions AS version
        SET superseded_by_version_id = ordered_versions.successor_id,
            superseded_at = ordered_versions.successor_retrieved_at
        FROM ordered_versions
        WHERE version.id = ordered_versions.id
          AND ordered_versions.successor_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Restore the fully immutable generic historical-snapshot guard."""
    op.drop_index("ix_trial_versions_nct_id_superseded_at", "trial_versions")
    op.drop_constraint("ck_trial_versions_supersession_pair", "trial_versions")
    op.drop_constraint("fk_trial_versions_superseded_by_version_id", "trial_versions")
    op.execute("DROP TRIGGER protect_trial_versions_history ON trial_versions")
    op.execute("DROP FUNCTION prevent_trial_version_source_changes()")
    op.execute(
        """
        CREATE TRIGGER protect_trial_versions_history
        BEFORE UPDATE OR DELETE ON trial_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_historical_snapshot_changes();
        """
    )
    op.drop_column("trial_versions", "superseded_at")
    op.drop_column("trial_versions", "superseded_by_version_id")
