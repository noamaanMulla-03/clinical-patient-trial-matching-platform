"""Create criteria, match evidence, and reviewer-decision tables.

Revision ID: 20260822_02
Revises: 20260822_01
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260822_02"
down_revision = "20260822_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create source-linked matching and reviewer-correction tables."""
    op.create_table(
        "criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("parsed_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("parser_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "requires_human_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("category IN ('inclusion', 'exclusion')", name="ck_criteria_category"),
        sa.CheckConstraint("source_start >= 0", name="ck_criteria_source_start"),
        sa.CheckConstraint("source_end > source_start", name="ck_criteria_source_span"),
        sa.ForeignKeyConstraint(
            ["trial_version_id"], ["trial_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_criteria_trial_version_id_category",
        "criteria",
        ["trial_version_id", "category"],
        unique=False,
    )
    op.create_table(
        "match_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "configuration_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_match_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["patient_import_id"], ["patient_imports.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_match_runs_patient_import_id_created_at",
        "match_runs",
        ["patient_import_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "trial_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_rank", sa.Integer(), nullable=False),
        sa.Column(
            "retrieval_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("candidate_rank > 0", name="ck_trial_matches_candidate_rank"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('potential_match', 'likely_excluded', 'needs_review', 'not_relevant')",
            name="ck_trial_matches_outcome",
        ),
        sa.ForeignKeyConstraint(["match_run_id"], ["match_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["trial_version_id"], ["trial_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_trial_matches_run_trial_version",
        "trial_matches",
        ["match_run_id", "trial_version_id"],
        unique=True,
    )
    op.create_index(
        "ix_trial_matches_run_rank",
        "trial_matches",
        ["match_run_id", "candidate_rank"],
        unique=False,
    )
    op.create_table(
        "criterion_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "evidence_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("evaluator_version", sa.String(length=128), nullable=False),
        sa.Column("evaluation_path", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "requires_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_criterion_results_outcome",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_fact_ids) = 'array'",
            name="ck_criterion_results_evidence_array",
        ),
        sa.CheckConstraint(
            "outcome = 'unknown' OR jsonb_array_length(evidence_fact_ids) > 0",
            name="ck_criterion_results_evidence_required",
        ),
        sa.ForeignKeyConstraint(
            ["criterion_id"], ["criteria.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["trial_match_id"], ["trial_matches.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_criterion_results_match_criterion",
        "criterion_results",
        ["trial_match_id", "criterion_id"],
        unique=True,
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("criterion_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", sa.String(length=128), nullable=False),
        sa.Column("previous_outcome", sa.String(length=16), nullable=False),
        sa.Column("corrected_outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "previous_outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_review_decisions_previous_outcome",
        ),
        sa.CheckConstraint(
            "corrected_outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_review_decisions_corrected_outcome",
        ),
        sa.CheckConstraint(
            "previous_outcome <> corrected_outcome",
            name="ck_review_decisions_outcome_changed",
        ),
        sa.ForeignKeyConstraint(
            ["criterion_result_id"], ["criterion_results.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_decisions_criterion_result_id_created_at",
        "review_decisions",
        ["criterion_result_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove matching and reviewer-correction tables."""
    op.drop_index(
        "ix_review_decisions_criterion_result_id_created_at",
        table_name="review_decisions",
    )
    op.drop_table("review_decisions")
    op.drop_index(
        "uq_criterion_results_match_criterion", table_name="criterion_results"
    )
    op.drop_table("criterion_results")
    op.drop_index("ix_trial_matches_run_rank", table_name="trial_matches")
    op.drop_index("uq_trial_matches_run_trial_version", table_name="trial_matches")
    op.drop_table("trial_matches")
    op.drop_index(
        "ix_match_runs_patient_import_id_created_at", table_name="match_runs"
    )
    op.drop_table("match_runs")
    op.drop_index("ix_criteria_trial_version_id_category", table_name="criteria")
    op.drop_table("criteria")
