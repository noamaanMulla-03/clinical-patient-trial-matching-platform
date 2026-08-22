"""Create synthetic patient import and versioned trial source tables.

Revision ID: 20260822_01
Revises:
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260822_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create source-linked patient and trial persistence tables."""
    op.create_table(
        "patients",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("synthetic", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("synthetic IS TRUE", name="ck_patients_synthetic_only"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_ref"),
    )
    op.create_table(
        "trials",
        sa.Column("nct_id", sa.String(length=16), nullable=False),
        sa.Column("current_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nct_id"),
    )
    op.create_table(
        "patient_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("fhir_version", sa.String(length=32), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_bundle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_patient_imports_status",
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_imports_patient_id_created_at",
        "patient_imports",
        ["patient_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "patient_facts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("patient_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["patient_import_id"], ["patient_imports.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_patient_facts_patient_id_effective_at",
        "patient_facts",
        ["patient_id", "effective_at"],
        unique=False,
    )
    op.create_index(
        "ix_patient_facts_patient_import_id",
        "patient_facts",
        ["patient_import_id"],
        unique=False,
    )
    op.create_table(
        "trial_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nct_id", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_study", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["nct_id"], ["trials.nct_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trial_versions_nct_id_ingested_at",
        "trial_versions",
        ["nct_id", "ingested_at"],
        unique=False,
    )
    op.create_index(
        "uq_trial_versions_nct_id_source_hash",
        "trial_versions",
        ["nct_id", "source_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Remove the source-linked patient and trial tables."""
    op.drop_index("uq_trial_versions_nct_id_source_hash", table_name="trial_versions")
    op.drop_index("ix_trial_versions_nct_id_ingested_at", table_name="trial_versions")
    op.drop_table("trial_versions")
    op.drop_index("ix_patient_facts_patient_import_id", table_name="patient_facts")
    op.drop_index("ix_patient_facts_patient_id_effective_at", table_name="patient_facts")
    op.drop_table("patient_facts")
    op.drop_index("ix_patient_imports_patient_id_created_at", table_name="patient_imports")
    op.drop_table("patient_imports")
    op.drop_table("trials")
    op.drop_table("patients")
