"""Persistent models for synthetic patient imports, trials, and match evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Patient(Base):
    """A stable synthetic-patient identity; clinical state comes from imports."""

    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint("synthetic IS TRUE", name="ck_patients_synthetic_only"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(128), unique=True)
    synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatientImport(Base):
    """An immutable source Bundle with mutable processing status and completion time."""

    __tablename__ = "patient_imports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_patient_imports_status",
        ),
        Index("ix_patient_imports_patient_id_created_at", "patient_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    fhir_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_bundle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PatientFactRecord(Base):
    """An immutable normalized fact traceable to one FHIR import snapshot."""

    __tablename__ = "patient_facts"
    __table_args__ = (
        Index("ix_patient_facts_patient_id_effective_at", "patient_id", "effective_at"),
        Index("ix_patient_facts_patient_import_id", "patient_import_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    patient_import_id: Mapped[UUID] = mapped_column(
        ForeignKey("patient_imports.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    value: Mapped[Any | None] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(64))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Trial(Base):
    """A mutable current projection; source history lives in TrialVersion records."""

    __tablename__ = "trials"

    nct_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    current_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrialVersion(Base):
    """An immutable ClinicalTrials.gov source snapshot for a trial version."""

    __tablename__ = "trial_versions"
    __table_args__ = (
        Index("ix_trial_versions_nct_id_ingested_at", "nct_id", "ingested_at"),
        Index(
            "uq_trial_versions_nct_id_source_hash", "nct_id", "source_hash", unique=True
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    nct_id: Mapped[str] = mapped_column(
        ForeignKey("trials.nct_id", ondelete="RESTRICT"), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_study: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Criterion(Base):
    """An immutable atomic clause from one immutable trial-version snapshot."""

    __tablename__ = "criteria"
    __table_args__ = (
        CheckConstraint(
            "category IN ('inclusion', 'exclusion')", name="ck_criteria_category"
        ),
        CheckConstraint("source_start >= 0", name="ck_criteria_source_start"),
        CheckConstraint("source_end > source_start", name="ck_criteria_source_span"),
        Index("ix_criteria_trial_version_id_category", "trial_version_id", "category"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trial_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("trial_versions.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MatchRun(Base):
    """Fixed matching inputs with mutable operational status and timestamps."""

    __tablename__ = "match_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_match_runs_status",
        ),
        Index(
            "ix_match_runs_patient_import_id_created_at",
            "patient_import_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    patient_import_id: Mapped[UUID] = mapped_column(
        ForeignKey("patient_imports.id", ondelete="RESTRICT"), nullable=False
    )
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    terminology_mapping_version: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_configuration_version: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="queued"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialMatch(Base):
    """Fixed retrieval inputs with an outcome mutable only while the run is active."""

    __tablename__ = "trial_matches"
    __table_args__ = (
        CheckConstraint("candidate_rank > 0", name="ck_trial_matches_candidate_rank"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('potential_match', 'likely_excluded', 'needs_review', 'not_relevant')",
            name="ck_trial_matches_outcome",
        ),
        Index(
            "uq_trial_matches_run_trial_version",
            "match_run_id",
            "trial_version_id",
            unique=True,
        ),
        Index("ix_trial_matches_run_rank", "match_run_id", "candidate_rank"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    match_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("match_runs.id", ondelete="RESTRICT"), nullable=False
    )
    trial_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("trial_versions.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CriterionResult(Base):
    """An immutable source-grounded result corrected through review records."""

    __tablename__ = "criterion_results"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_criterion_results_outcome",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_fact_ids) = 'array'",
            name="ck_criterion_results_evidence_array",
        ),
        # A non-unknown result without evidence could falsely reassure a reviewer.
        CheckConstraint(
            "outcome = 'unknown' OR jsonb_array_length(evidence_fact_ids) > 0",
            name="ck_criterion_results_evidence_required",
        ),
        Index(
            "uq_criterion_results_match_criterion",
            "trial_match_id",
            "criterion_id",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    trial_match_id: Mapped[UUID] = mapped_column(
        ForeignKey("trial_matches.id", ondelete="RESTRICT"), nullable=False
    )
    criterion_id: Mapped[UUID] = mapped_column(
        ForeignKey("criteria.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_fact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_path: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    requires_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewDecision(Base):
    """An append-only immutable reviewer correction for a criterion result."""

    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "previous_outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_review_decisions_previous_outcome",
        ),
        CheckConstraint(
            "corrected_outcome IN ('met', 'not_met', 'unknown', 'conflicting')",
            name="ck_review_decisions_corrected_outcome",
        ),
        CheckConstraint(
            "previous_outcome <> corrected_outcome",
            name="ck_review_decisions_outcome_changed",
        ),
        Index(
            "ix_review_decisions_criterion_result_id_created_at",
            "criterion_result_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    criterion_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("criterion_results.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    corrected_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
