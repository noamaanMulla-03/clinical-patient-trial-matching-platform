"""Safe API contracts for asynchronous lexical and semantic match runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import MatchRun, TrialMatch

RetrievalField = Literal["conditions", "title", "interventions", "eligibility_text"]
_RETRIEVAL_FIELDS: tuple[RetrievalField, ...] = (
    "conditions",
    "title",
    "interventions",
    "eligibility_text",
)


class MatchRunCreateRequest(BaseModel):
    """Select one completed synthetic patient import for candidate retrieval."""

    model_config = ConfigDict(extra="forbid")

    patient_import_id: UUID


class MatchRunFailureResponse(BaseModel):
    """Static terminal failure details that never contain clinical input text."""

    code: str
    message: str


class MatchRunResponse(BaseModel):
    """Durable operational status without exposing normalized clinical input text."""

    id: UUID
    patient_import_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    candidate_count: int = Field(ge=0)
    cancellation_requested: bool
    configuration_versions: dict[str, str]
    created_at: datetime
    candidate_limit: int = Field(gt=0)
    started_at: datetime | None = None
    failure: MatchRunFailureResponse | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_record(
        cls, run: MatchRun, *, candidate_count: int, cancellation_requested: bool
    ) -> MatchRunResponse:
        candidate_limit = run.configuration_snapshot["candidate_limit"]
        if type(candidate_limit) is not int or candidate_limit < 1:
            raise ValueError("Match run has an invalid candidate limit.")
        failure = (
            MatchRunFailureResponse(code=run.failure_code, message=run.failure_message)
            if run.failure_code is not None and run.failure_message is not None
            else None
        )
        return cls(
            patient_import_id=run.patient_import_id,
            id=run.id,
            status=cast(
                Literal["queued", "running", "completed", "failed", "cancelled"],
                run.status,
            ),
            candidate_count=candidate_count,
            cancellation_requested=cancellation_requested,
            candidate_limit=candidate_limit,
            configuration_versions={
                "parser": run.parser_version,
                "retrieval": run.retrieval_version,
                "rule_engine": run.rule_engine_version,
                "terminology_mapping": run.terminology_mapping_version,
                "prompt": run.prompt_version,
                "model_configuration": run.model_configuration_version,
            },
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            failure=failure,
        )


class TrialMatchResponse(BaseModel):
    """A ranked retrieval candidate; this is not an eligibility or enrollment result."""

    id: UUID
    trial_version_id: UUID
    patient_id: str
    nct_id: str
    title: str | None = None
    study_status: str | None = None
    source_updated_at: datetime | None = None
    candidate_rank: int = Field(gt=0)
    retrieval_scores: dict[str, Any]
    retrieval_sources: list[Literal["lexical", "semantic"]] = Field(
        default_factory=list
    )
    retrieval_relevance: RetrievalRelevanceResponse | None = None
    semantic_relevance: SemanticRetrievalRelevanceResponse | None = None
    criterion_results: list[CriterionResultSummary] = Field(default_factory=list)
    outcome: (
        Literal["potential_match", "likely_excluded", "needs_review", "not_relevant"]
        | None
    ) = None
    created_at: datetime
    evaluated_at: datetime | None = None

    @classmethod
    def from_record(
        cls,
        match: TrialMatch,
        *,
        patient_id: str,
        nct_id: str,
        title: str | None,
        study_status: str | None,
        source_updated_at: datetime | None,
        criterion_results: list[CriterionResultSummary] | None = None,
    ) -> TrialMatchResponse:
        return cls(
            patient_id=patient_id,
            id=match.id,
            trial_version_id=match.trial_version_id,
            nct_id=nct_id,
            title=title,
            study_status=study_status,
            source_updated_at=source_updated_at,
            candidate_rank=match.candidate_rank,
            retrieval_scores=match.retrieval_scores,
            retrieval_sources=_retrieval_sources(match.retrieval_scores),
            retrieval_relevance=RetrievalRelevanceResponse.from_scores(
                match.retrieval_scores
            ),
            semantic_relevance=SemanticRetrievalRelevanceResponse.from_scores(
                match.retrieval_scores
            ),
            criterion_results=criterion_results or [],
            outcome=cast(
                Literal[
                    "potential_match",
                    "likely_excluded",
                    "needs_review",
                    "not_relevant",
                ]
                | None,
                match.outcome,
            ),
            created_at=match.created_at,
            evaluated_at=match.evaluated_at,
        )


class RetrievalRelevanceResponse(BaseModel):
    """Deterministic lexical relevance, separate from any review outcome."""

    score: float = Field(ge=0)
    matched_term_count: int = Field(ge=0)
    query_term_count: int = Field(ge=0)
    matched_fields: list[RetrievalField] = Field(default_factory=list)
    matched_fact_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_scores(cls, scores: dict[str, Any]) -> RetrievalRelevanceResponse | None:
        """Expose validated score fields; absent or corrupt values stay unavailable."""
        score = scores.get("lexical_score")
        matched_term_count = scores.get("matched_term_count")
        query_term_count = scores.get("query_term_count")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or score < 0
            or type(matched_term_count) is not int
            or matched_term_count < 0
            or type(query_term_count) is not int
            or query_term_count < 0
        ):
            return None
        return cls(
            score=float(score),
            matched_term_count=matched_term_count,
            query_term_count=query_term_count,
            matched_fields=_matched_fields(scores.get("field_matches")),
            matched_fact_ids=_matched_fact_ids(scores.get("matched_fact_ids")),
        )


class SemanticRetrievalRelevanceResponse(BaseModel):
    """Cosine similarity from a transient query, never a clinical outcome."""

    score: float = Field(ge=-1, le=1)
    rank: int = Field(gt=0)

    @classmethod
    def from_scores(
        cls, scores: dict[str, Any]
    ) -> SemanticRetrievalRelevanceResponse | None:
        score = scores.get("semantic_score")
        rank = scores.get("semantic_rank")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or score < -1
            or score > 1
            or type(rank) is not int
            or rank < 1
        ):
            return None
        return cls(score=float(score), rank=rank)


class CriterionResultSummary(BaseModel):
    """A reviewable criterion link attached to the exact trial-match snapshot."""

    id: UUID
    category: Literal["inclusion", "exclusion"]
    source_text: str
    outcome: Literal["met", "not_met", "unknown", "conflicting"]
    current_outcome: Literal["met", "not_met", "unknown", "conflicting"]
    requires_review: bool


def _matched_fields(value: Any) -> list[RetrievalField]:
    """Expose only validated deterministic field labels as retrieval rationale."""
    if not isinstance(value, Mapping):
        return []
    fields: list[RetrievalField] = []
    for field_name in _RETRIEVAL_FIELDS:
        count = value.get(field_name)
        if type(count) is int and count > 0:
            fields.append(field_name)
    return fields


def _retrieval_sources(value: dict[str, Any]) -> list[Literal["lexical", "semantic"]]:
    sources = value.get("candidate_sources")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        return []
    return list(
        dict.fromkeys(source for source in sources if source in {"lexical", "semantic"})
    )


def _matched_fact_ids(value: Any) -> list[str]:
    """Keep only stable fact IDs; raw patient values stay out of result cards."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))
