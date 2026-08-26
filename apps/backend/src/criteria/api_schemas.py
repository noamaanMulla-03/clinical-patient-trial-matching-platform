"""Reviewer-safe API contracts for source-linked criterion detail."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.criteria.schemas import CriterionCategory, CriterionOutcome
from src.fhir.schemas import PatientFactResponse


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
    reason: str
    evaluation_path: str | None = None


class CriterionDetailResponse(BaseModel):
    """Source, evaluation, snapshot evidence, and audit history for review."""

    patient_id: str
    trial_match_id: UUID
    criterion: CriterionSourceResponse
    evaluation: CriterionEvaluationResponse
    patient_evidence: list[PatientFactResponse]
    audit_history: list[CriterionAuditEventResponse]


class ReviewCorrectionRequest(BaseModel):
    """Append one reviewer correction without changing the deterministic result."""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(min_length=1, max_length=128)
    corrected_outcome: CriterionOutcome
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reviewer_id", "reason", mode="before")
    @classmethod
    def normalize_reviewer_text(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value


class ReviewCorrectionResponse(BaseModel):
    """Confirmation of an immutable reviewer correction record."""

    id: UUID
    criterion_result_id: UUID
    reviewer_id: str
    previous_outcome: CriterionOutcome
    corrected_outcome: CriterionOutcome
    reason: str
    created_at: datetime
