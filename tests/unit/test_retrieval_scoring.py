"""Deterministic lexical scoring checks for ranked trial candidates."""

import pytest

from app.db.models import Trial
from app.retrieval.lexical import lexical_trial_candidates_statement
from app.retrieval.schemas import (
    PatientDerivedRetrievalQuery,
    RetrievalTerm,
)
from app.retrieval.scoring import rank_scored_trials, score_trial_candidate


def _trial(
    nct_id: str,
    *,
    title: str = "",
    conditions: list[str] | None = None,
    interventions: list[dict[str, object]] | None = None,
    eligibility_text: str = "",
) -> Trial:
    return Trial(
        nct_id=nct_id,
        title=title or None,
        conditions=conditions or [],
        interventions=interventions or [],
        eligibility_text=eligibility_text or None,
    )


def test_scoring_uses_best_field_weight_per_patient_fact_term() -> None:
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text="Diabetes",
                source_fact_id="condition-fact",
                kind="condition",
            ),
            RetrievalTerm(
                text="Metformin",
                source_fact_id="medication-fact",
                kind="medication",
            ),
        ]
    )
    trial = _trial(
        "NCT00000001",
        title="Diabetes study",
        conditions=["Diabetes mellitus"],
        interventions=[{"name": "Metformin", "type": "DRUG"}],
    )

    score = score_trial_candidate(trial, query)

    assert score == {
        "lexical_score": 6.0,
        "matched_term_count": 2,
        "query_term_count": 2,
        "field_matches": {
            "conditions": 1,
            "title": 0,
            "interventions": 1,
            "eligibility_text": 0,
        },
        "matched_fact_ids": ["condition-fact", "medication-fact"],
    }


def test_ranking_is_deterministic_and_empty_queries_do_not_scan_trials() -> None:
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text="Diabetes",
                source_fact_id="condition-fact",
                kind="condition",
            )
        ]
    )
    first = _trial("NCT00000001", conditions=["Diabetes mellitus"])
    second = _trial("NCT00000002", title="Diabetes study")
    first_score = score_trial_candidate(first, query)
    second_score = score_trial_candidate(second, query)

    assert first_score is not None and second_score is not None
    assert [
        trial.nct_id
        for trial, _ in rank_scored_trials(
            [(second, second_score), (first, first_score)]
        )
    ] == [
        "NCT00000001",
        "NCT00000002",
    ]
    empty_statement = lexical_trial_candidates_statement(
        PatientDerivedRetrievalQuery(), candidate_limit=10
    )
    assert "false" in str(empty_statement.compile()).lower()


def test_lexical_candidate_selection_is_deterministically_capped() -> None:
    query = PatientDerivedRetrievalQuery(
        terms=[
            RetrievalTerm(
                text="Diabetes", source_fact_id="condition-fact", kind="condition"
            )
        ]
    )
    statement = lexical_trial_candidates_statement(query, candidate_limit=2)
    compiled = statement.compile()

    assert "order by trials.nct_id" in str(compiled).lower()
    assert 2 in compiled.params.values()
    with pytest.raises(ValueError, match="candidate_limit must be positive"):
        lexical_trial_candidates_statement(
            PatientDerivedRetrievalQuery(), candidate_limit=0
        )
