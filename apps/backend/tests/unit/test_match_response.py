"""Safe result-list response projections."""

from datetime import UTC, datetime
from uuid import uuid4

from src.db.models import MatchRun, TrialMatch
from src.matching.schemas import MatchRunResponse, TrialMatchResponse


def test_result_response_separates_retrieval_relevance_from_review_outcome() -> None:
    """A lexical score is displayed as relevance, never recast as an outcome."""
    match = TrialMatch(
        id=uuid4(),
        match_run_id=uuid4(),
        trial_version_id=uuid4(),
        candidate_rank=1,
        retrieval_scores={
            "lexical_score": 6.0,
            "matched_term_count": 2,
            "query_term_count": 3,
            "candidate_sources": ["lexical", "semantic"],
            "semantic_score": 0.75,
            "semantic_rank": 2,
            "reciprocal_rank_fusion_score": 0.0325,
            "reciprocal_rank_fusion_rank": 1,
            "reciprocal_rank_fusion_rank_constant": 60,
            "reciprocal_rank_fusion_version": "reciprocal-rank-fusion-v1",
        },
        outcome="needs_review",
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    response = TrialMatchResponse.from_record(
        match,
        patient_id="synthetic-patient-1",
        nct_id="NCT00000001",
        title="Synthetic diabetes study",
        study_status="RECRUITING",
        source_updated_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert response.outcome == "needs_review"
    assert response.retrieval_sources == ["lexical", "semantic"]
    assert response.retrieval_relevance is not None
    assert response.retrieval_relevance.model_dump() == {
        "score": 6.0,
        "matched_term_count": 2,
        "query_term_count": 3,
        "matched_fields": [],
        "matched_fact_ids": [],
    }
    assert response.patient_id == "synthetic-patient-1"
    assert response.study_status == "RECRUITING"
    assert response.source_updated_at == datetime(2026, 8, 22, tzinfo=UTC)
    assert response.semantic_relevance is not None
    assert response.semantic_relevance.model_dump() == {"score": 0.75, "rank": 2}
    assert response.fused_relevance is not None
    assert response.fused_relevance.model_dump() == {
        "method": "reciprocal-rank-fusion-v1",
        "score": 0.0325,
        "rank": 1,
        "rank_constant": 60,
    }


def test_match_run_response_hides_internal_patient_fact_identifiers() -> None:
    run = MatchRun(
        id=uuid4(),
        patient_import_id=uuid4(),
        configuration_snapshot={"candidate_limit": 100},
        retrieval_execution={
            "mode": "hybrid",
            "query_manifest_hash": "safe-hash",
            "query_manifest": {
                "included_fact_ids": ["internal-fact-1"],
                "omitted_fact_ids": ["internal-fact-2", "internal-fact-3"],
                "term_kinds": ["condition"],
            },
        },
        parser_version="manual-v1",
        retrieval_version="hybrid-v1",
        rule_engine_version="deterministic-v1",
        terminology_mapping_version="source-coded-v1",
        prompt_version="not-used-v1",
        model_configuration_version="not-used-v1",
        status="completed",
        created_at=datetime(2026, 8, 28, tzinfo=UTC),
    )

    response = MatchRunResponse.from_record(
        run, candidate_count=1, cancellation_requested=False
    )

    assert "query_manifest" not in response.retrieval_execution
    assert "internal-fact-1" not in response.model_dump_json()
    assert response.retrieval_execution["query_summary"] == {
        "included_fact_count": 1,
        "omitted_fact_count": 2,
        "term_kinds": ["condition"],
    }


def test_result_response_keeps_invalid_retrieval_relevance_unavailable() -> None:
    """Malformed persisted score data must not become a plausible relevance value."""
    match = TrialMatch(
        id=uuid4(),
        match_run_id=uuid4(),
        trial_version_id=uuid4(),
        candidate_rank=1,
        retrieval_scores={"lexical_score": "high"},
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    response = TrialMatchResponse.from_record(
        match,
        patient_id="synthetic-patient-1",
        nct_id="NCT00000001",
        title=None,
        study_status=None,
        source_updated_at=None,
    )

    assert response.retrieval_relevance is None


def test_result_response_exposes_explainable_retrieval_fields_and_fact_links() -> None:
    match = TrialMatch(
        id=uuid4(),
        match_run_id=uuid4(),
        trial_version_id=uuid4(),
        candidate_rank=1,
        retrieval_scores={
            "lexical_score": 5.0,
            "matched_term_count": 1,
            "query_term_count": 2,
            "field_matches": {"conditions": 1, "title": 0},
            "matched_fact_ids": ["fact-1", "fact-1", "fact-2"],
        },
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    response = TrialMatchResponse.from_record(
        match,
        patient_id="synthetic-patient-1",
        nct_id="NCT00000001",
        title="Synthetic diabetes study",
        study_status="RECRUITING",
        source_updated_at=None,
    )

    assert response.retrieval_relevance is not None
    assert response.retrieval_relevance.matched_fields == ["conditions"]
    assert response.retrieval_relevance.matched_fact_ids == ["fact-1", "fact-2"]
