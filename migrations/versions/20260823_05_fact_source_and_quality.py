"""Store source-resource copies and explicit normalized-data quality findings.

Revision ID: 20260823_05
Revises: 20260822_04
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_05"
down_revision = "20260822_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make each normalized fact independently reviewable and quality-aware."""
    op.add_column(
        "patient_imports",
        sa.Column(
            "data_quality",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "patient_facts",
        sa.Column(
            "source_resource",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # Reconstruct each historical source resource only from its immutable Bundle.
    # A migration must fail rather than fabricate clinical source evidence.
    op.execute(
        """
        UPDATE patient_facts AS fact
        SET source_resource = bundle_entry.resource_entry -> 'resource'
        FROM patient_imports AS source_import
        CROSS JOIN LATERAL jsonb_array_elements(source_import.source_bundle -> 'entry')
            AS bundle_entry(resource_entry)
        WHERE fact.patient_import_id = source_import.id
          AND bundle_entry.resource_entry -> 'resource' ->> 'resourceType'
              = fact.provenance ->> 'resource_type'
          AND bundle_entry.resource_entry -> 'resource' ->> 'id'
              = fact.provenance ->> 'resource_id'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM patient_facts WHERE source_resource IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot backfill patient_facts.source_resource from source bundles';
            END IF;
        END;
        $$;
        """
    )
    op.alter_column("patient_facts", "source_resource", nullable=False)
    op.add_column(
        "patient_facts",
        sa.Column(
            "normalization",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "patient_facts",
        sa.Column(
            "quality_issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_patient_import_source_changes()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.fhir_version IS DISTINCT FROM OLD.fhir_version
               OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
               OR NEW.source_bundle IS DISTINCT FROM OLD.source_bundle
               OR NEW.data_quality IS DISTINCT FROM OLD.data_quality
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'patient import source snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    """Remove source-resource copies and their derived quality metadata."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_patient_import_source_changes()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.fhir_version IS DISTINCT FROM OLD.fhir_version
               OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
               OR NEW.source_bundle IS DISTINCT FROM OLD.source_bundle
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'patient import source snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.drop_column("patient_facts", "quality_issues")
    op.drop_column("patient_facts", "normalization")
    op.drop_column("patient_facts", "source_resource")
    op.drop_column("patient_imports", "data_quality")
