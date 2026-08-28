"""Safety and traceability checks for structured retrieval re-ranking."""

from src.db.models import Trial
from src.retrieval.reranking import (
    STRUCTURED_EVIDENCE_RERANKER_VERSION,
    rerank_fused_trial_candidates,
)
from src.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm


def test_direct_structured_condition_support_promotes_a_candidate_with_fact_trace() -> (
    None
):
    semantic_first = Trial(
        nct_id="NCT00000001",
        title="General metabolism study",
        conditions=[],
        interventions=[],
    )
    supported_second = Trial(
        nct_id="NCT00000002",
        title="Diabetes study",
        conditions=["Diabetes mellitus"],
        interventions=[],
    )
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text="Diabetes mellitus", source_fact_id="fact-1", kind="condition"
            )
        ]
    )

    reranked = rerank_fused_trial_candidates(
        [
            (semantic_first, {"reciprocal_rank_fusion_rank": 1}),
            (supported_second, {"reciprocal_rank_fusion_rank": 2}),
        ],
        query,
        candidate_limit=2,
    )

    assert [trial.nct_id for trial, _ in reranked] == ["NCT00000002", "NCT00000001"]
    supported_scores = reranked[0][1]
    assert supported_scores["structured_evidence_reranker_version"] == (
        STRUCTURED_EVIDENCE_RERANKER_VERSION
    )
    assert supported_scores["structured_evidence_status"] == "direct_support"
    assert supported_scores["structured_evidence_support_tier"] == 3
    assert supported_scores["structured_evidence_supported_fields"] == ["conditions"]
    assert supported_scores["structured_evidence_supporting_fact_ids"] == ["fact-1"]
    assert supported_scores["structured_evidence_reranker_input_rank"] == 2
    assert supported_scores["structured_evidence_reranker_rank"] == 1


def test_unknown_structured_support_is_retained_without_penalty() -> None:
    first = Trial(nct_id="NCT00000001", conditions=[], interventions=[])
    second = Trial(nct_id="NCT00000002", conditions=[], interventions=[])
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(text="Diabetes", source_fact_id="fact-1", kind="condition")
        ]
    )

    reranked = rerank_fused_trial_candidates(
        [(first, {}), (second, {})], query, candidate_limit=2
    )

    assert [trial.nct_id for trial, _ in reranked] == ["NCT00000001", "NCT00000002"]
    assert reranked[0][1]["structured_evidence_status"] == "unknown"
    assert reranked[0][1]["structured_evidence_support_tier"] == 0
    assert "without a penalty" in reranked[0][1]["structured_evidence_note"]


def test_partial_words_do_not_count_as_direct_structured_support() -> None:
    trial = Trial(
        nct_id="NCT00000001",
        title="Pancreatic study",
        conditions=["Pancreatic cancer"],
        interventions=[],
    )
    query = PatientDerivedRetrievalQuery(
        terms=[RetrievalTerm(text="ana", source_fact_id="fact-1", kind="condition")]
    )

    reranked = rerank_fused_trial_candidates([(trial, {})], query, candidate_limit=1)

    assert reranked[0][1]["structured_evidence_status"] == "unknown"


def test_medication_facts_support_interventions_not_trial_conditions() -> None:
    condition_only = Trial(
        nct_id="NCT00000001",
        conditions=["Metformin exposure"],
        interventions=[],
    )
    intervention_supported = Trial(
        nct_id="NCT00000002",
        conditions=[],
        interventions=[{"name": "Metformin"}],
    )
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(text="Metformin", source_fact_id="fact-1", kind="medication")
        ]
    )

    reranked = rerank_fused_trial_candidates(
        [(condition_only, {}), (intervention_supported, {})],
        query,
        candidate_limit=2,
    )

    assert [trial.nct_id for trial, _ in reranked] == ["NCT00000002", "NCT00000001"]
    assert reranked[0][1]["structured_evidence_support_tier"] == 2
    assert reranked[1][1]["structured_evidence_status"] == "unknown"
