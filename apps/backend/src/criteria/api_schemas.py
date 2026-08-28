"""Reviewer-safe API contracts for source-linked criterion detail."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.criteria.schemas import (
    CriterionCategory,
    CriterionOutcome,
    CriterionReviewReason,
)
from src.fhir.schemas import PatientFactResponse

ReviewCorrectionReason = Literal[
    "evidence_missing",
    "evidence_conflicting",
    "evidence_stale",
    "source_span_issue",
    "source_data_issue",
    "other_nonclinical_review_issue",
]


class CriterionSourceResponse(BaseModel):
    """Exact original trial text and parser output for one atomic criterion."""

    id: UUID
    category: CriterionCategory
    source_text: str
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    parsed_data: dict[str, Any]
    parser_version: str
    parser_confidence: Decimal | None = None
    requires_human_review: bool
    review_reasons: list[CriterionReviewReason]
    created_at: datetime


class ParserProvenanceResponse(BaseModel):
    """Versioned deterministic parser output retained with a public trial source."""

    parser_version: str
    prompt_version: str
    model_configuration_version: str
    raw_output: dict[str, Any]
    created_at: datetime


class CriterionEvaluationResponse(BaseModel):
    """The immutable deterministic result; not an enrollment decision."""

    id: UUID
    outcome: CriterionOutcome
    current_outcome: CriterionOutcome
    evidence_fact_ids: list[str]
    evaluator_version: str
    evaluation_path: str
    explanation: str
    requires_review: bool
    evaluated_at: datetime


class CriterionAuditEventResponse(BaseModel):
    """An immutable evaluation or reviewer-correction history item."""

    id: UUID
    event_type: Literal["deterministic_evaluation", "review_correction"]
    occurred_at: datetime
    actor_id: str
    outcome: CriterionOutcome
    previous_outcome: CriterionOutcome | None = None
    reason_code: str
    evaluation_path: str | None = None


class CriterionDetailResponse(BaseModel):
    """Source, evaluation, snapshot evidence, and audit history for review."""

    patient_id: str
    trial_match_id: UUID
    criterion: CriterionSourceResponse
    parser_provenance: ParserProvenanceResponse | None = None
    evaluation: CriterionEvaluationResponse
    patient_evidence: list[PatientFactResponse]
    audit_history: list[CriterionAuditEventResponse]


class ReviewCorrectionRequest(BaseModel):
    """Append one reviewer correction without changing the deterministic result."""

    model_config = ConfigDict(extra="forbid")

    corrected_outcome: CriterionOutcome
    reason_code: ReviewCorrectionReason


class ReviewCorrectionResponse(BaseModel):
    """Confirmation of an immutable reviewer correction record."""

    id: UUID
    criterion_result_id: UUID
    reviewer_id: str
    previous_outcome: CriterionOutcome
    corrected_outcome: CriterionOutcome
    reason_code: ReviewCorrectionReason
    created_at: datetime
