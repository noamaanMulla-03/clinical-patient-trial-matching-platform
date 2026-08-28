"""Typed atomic criteria retained before any automated eligibility-text parsing."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.criteria.units import UnitCompatibilityError, validate_lab_unit
from src.fhir.schemas import ClinicalCode

CriterionCategory = Literal["inclusion", "exclusion"]
CriterionReviewReason = Literal[
    "ambiguous_clause",
    "nested_clause",
    "low_confidence_parse",
]
CriterionOutcome = Literal["met", "not_met", "unknown", "conflicting"]
CriterionEvaluationReason = Literal[
    "predicate_matched",
    "predicate_not_matched",
    "missing_evidence",
    "conflicting_evidence",
    "unusable_evidence",
    "ambiguous_age",
    "missing_date",
    "future_date",
    "undocumented_status",
]


class AgeRule(BaseModel):
    """A deterministic minimum or maximum age rule expressed in whole years."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["age"]
    operator: Literal["at_least", "at_most"]
    years: int = Field(ge=0, le=130)


class RecordedSexRule(BaseModel):
    """A rule against FHIR administrative gender without inferring biological sex."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["recorded_sex"]
    value: Literal["male", "female"]


class CodedConditionRule(BaseModel):
    """An exact code-system and code match for a recorded Condition fact."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["coded_condition"]
    code: ClinicalCode


class NumericLabThresholdRule(BaseModel):
    """A threshold over one exact-coded numeric Observation in one exact unit."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["numeric_lab_threshold"]
    code: ClinicalCode
    comparator: Literal[">", ">=", "<", "<="]
    threshold: float
    unit: str = Field(min_length=1)

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_supported_lab_unit(self) -> NumericLabThresholdRule:
        try:
            validate_lab_unit(
                system=self.code.system, code=self.code.value, unit=self.unit
            )
        except UnitCompatibilityError as error:
            raise ValueError(str(error)) from error
        return self


class DateWindowRule(BaseModel):
    """Require an exact-coded fact date to fall inside an inclusive date window."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["date_window"]
    fact_kind: Literal["condition", "observation", "medication", "procedure"]
    code: ClinicalCode
    starts_on: date | None = None
    ends_on: date | None = None

    @model_validator(mode="after")
    def require_bounded_window(self) -> DateWindowRule:
        if self.starts_on is None and self.ends_on is None:
            raise ValueError("A date window requires at least one boundary.")
        if (
            self.starts_on is not None
            and self.ends_on is not None
            and self.starts_on > self.ends_on
        ):
            raise ValueError("A date window start cannot be after its end.")
        return self


class RecencyWindowRule(BaseModel):
    """Require an exact-coded fact to be recorded within a supplied day window."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["recency_window"]
    fact_kind: Literal["condition", "observation", "medication", "procedure"]
    code: ClinicalCode
    within_days: int = Field(ge=0, le=36500)


class MedicationStatusRule(BaseModel):
    """Check only an explicitly documented medication code and FHIR status."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["medication_status"]
    code: ClinicalCode
    expected_status: Literal[
        "active",
        "on-hold",
        "completed",
        "stopped",
        "not-taken",
        "cancelled",
        "draft",
    ]


class ProcedureStatusRule(BaseModel):
    """Check only an explicitly documented procedure code and FHIR status."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["procedure_status"]
    code: ClinicalCode
    expected_status: Literal[
        "preparation",
        "in-progress",
        "not-done",
        "on-hold",
        "stopped",
        "completed",
    ]


CriterionRule = Annotated[
    AgeRule
    | RecordedSexRule
    | CodedConditionRule
    | NumericLabThresholdRule
    | DateWindowRule
    | RecencyWindowRule
    | MedicationStatusRule
    | ProcedureStatusRule,
    Field(discriminator="kind"),
]


class AtomicCriterion(BaseModel):
    """One human-authored clause with a rule and exact span in eligibility text."""

    model_config = ConfigDict(extra="forbid")

    category: CriterionCategory
    source_text: str = Field(min_length=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    rule: CriterionRule

    @model_validator(mode="after")
    def require_exact_source_span(self) -> AtomicCriterion:
        """Prevent a criterion from claiming a span that does not match its text."""
        if self.source_end <= self.source_start:
            raise ValueError("Criterion source_end must be greater than source_start.")
        if self.source_end - self.source_start != len(self.source_text):
            raise ValueError("Criterion source span must match source_text exactly.")
        return self


class CriterionEvaluation(BaseModel):
    """Evidence-grounded deterministic result; never a final enrollment decision."""

    model_config = ConfigDict(extra="forbid")

    outcome: CriterionOutcome
    evidence_fact_ids: list[str] = Field(default_factory=list)
    reason: CriterionEvaluationReason
    requires_review: bool

    @model_validator(mode="after")
    def require_evidence_for_resolved_outcomes(self) -> CriterionEvaluation:
        """Disallow a resolved outcome that cannot be traced to patient facts."""
        if self.outcome != "unknown" and not self.evidence_fact_ids:
            raise ValueError("Non-unknown criterion evaluations require evidence IDs.")
        if len(set(self.evidence_fact_ids)) != len(self.evidence_fact_ids):
            raise ValueError("Criterion evaluation evidence IDs must not be repeated.")
        return self
