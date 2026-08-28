"""Safe API contracts for asynchronous lexical and semantic match runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import MatchRun, TrialMatch

RetrievalField = Literal["conditions", "title", "interventions", "eligibility_text"]
_RETRIEVAL_FIELDS: tuple[RetrievalField, ...] = (
    "conditions",
    "title",
    "interventions",
    "eligibility_text",
)


def _review_safe_retrieval_execution(value: object) -> dict[str, Any]:
    """Project run metadata without exposing patient-fact identifiers.

    The stored manifest remains available for controlled audit and replay.  The
    reviewer status API deliberately exposes aggregate counts only, because the
    UI does not need internal fact identifiers to explain a retrieval run.
    """
    if not isinstance(value, Mapping):
        return {}

    response: dict[str, Any] = {}
    for key in (
        "mode",
        "degradation_reasons",
        "query_manifest_hash",
        "counts",
        "semantic_coverage",
        "catalogue",
    ):
        item = value.get(key)
        if item is not None:
            response[key] = item

    manifest = value.get("query_manifest")
    if isinstance(manifest, Mapping):
        included_fact_ids = manifest.get("included_fact_ids")
        omitted_fact_ids = manifest.get("omitted_fact_ids")
        response["query_summary"] = {
            "included_fact_count": (
                len(included_fact_ids) if isinstance(included_fact_ids, list) else 0
            ),
            "omitted_fact_count": (
                len(omitted_fact_ids) if isinstance(omitted_fact_ids, list) else 0
            ),
            "term_kinds": (
                manifest["term_kinds"]
                if isinstance(manifest.get("term_kinds"), list)
                else []
            ),
        }
    return response


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
    retrieval_execution: dict[str, Any] = Field(default_factory=dict)
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
            retrieval_execution=_review_safe_retrieval_execution(
                run.retrieval_execution
            ),
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
    fused_relevance: ReciprocalRankFusionRelevanceResponse | None = None
    structured_relevance: StructuredRetrievalRelevanceResponse | None = None
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
            fused_relevance=ReciprocalRankFusionRelevanceResponse.from_scores(
                match.retrieval_scores
            ),
            structured_relevance=StructuredRetrievalRelevanceResponse.from_scores(
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


class ReciprocalRankFusionRelevanceResponse(BaseModel):
    """Explain the combined retrieval position without implying an outcome."""

    method: Literal["reciprocal-rank-fusion-v1"]
    score: float = Field(gt=0)
    rank: int = Field(gt=0)
    rank_constant: int = Field(gt=0)

    @classmethod
    def from_scores(
        cls, scores: dict[str, Any]
    ) -> ReciprocalRankFusionRelevanceResponse | None:
        score = scores.get("reciprocal_rank_fusion_score")
        rank = scores.get("reciprocal_rank_fusion_rank")
        rank_constant = scores.get("reciprocal_rank_fusion_rank_constant")
        method = scores.get("reciprocal_rank_fusion_version")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or score <= 0
            or type(rank) is not int
            or rank < 1
            or type(rank_constant) is not int
            or rank_constant < 1
            or method != "reciprocal-rank-fusion-v1"
        ):
            return None
        return cls(
            method=method,
            score=float(score),
            rank=rank,
            rank_constant=rank_constant,
        )


class StructuredRetrievalRelevanceResponse(BaseModel):
    """Direct structured support used only to order review candidates."""

    method: Literal["structured-evidence-reranker-v2"]
    status: Literal["direct_support", "unknown"]
    support_tier: int = Field(ge=0, le=3)
    input_rank: int = Field(gt=0)
    rank: int = Field(gt=0)
    supported_fields: list[Literal["conditions", "title", "interventions"]] = Field(
        default_factory=list
    )
    supporting_fact_ids: list[str] = Field(default_factory=list)
    note: str

    @classmethod
    def from_scores(
        cls, scores: dict[str, Any]
    ) -> StructuredRetrievalRelevanceResponse | None:
        method = scores.get("structured_evidence_reranker_version")
        status = scores.get("structured_evidence_status")
        support_tier = scores.get("structured_evidence_support_tier")
        input_rank = scores.get("structured_evidence_reranker_input_rank")
        rank = scores.get("structured_evidence_reranker_rank")
        note = scores.get("structured_evidence_note")
        if (
            method != "structured-evidence-reranker-v2"
            or status not in {"direct_support", "unknown"}
            or type(support_tier) is not int
            or support_tier not in {0, 1, 2, 3}
            or type(input_rank) is not int
            or input_rank < 1
            or type(rank) is not int
            or rank < 1
            or not isinstance(note, str)
        ):
            return None
        fields = scores.get("structured_evidence_supported_fields")
        fact_ids = scores.get("structured_evidence_supporting_fact_ids")
        allowed_fields = {"conditions", "title", "interventions"}
        if not isinstance(fields, list) or not all(
            isinstance(field, str) and field in allowed_fields for field in fields
        ):
            return None
        if not isinstance(fact_ids, list) or not all(
            isinstance(fact_id, str) for fact_id in fact_ids
        ):
            return None
        return cls(
            method=method,
            status=status,
            support_tier=support_tier,
            input_rank=input_rank,
            rank=rank,
            supported_fields=fields,
            supporting_fact_ids=fact_ids,
            note=note,
        )


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
