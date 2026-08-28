"""Deterministic lexical scoring checks for ranked trial candidates."""

from datetime import UTC, datetime

import pytest

from src.db.models import Trial
from src.retrieval.lexical import lexical_trial_candidates_statement
from src.retrieval.schemas import (
    PatientDerivedRetrievalQuery,
    RetrievalTerm,
)
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate


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
        PatientDerivedRetrievalQuery(),
        candidate_limit=10,
        catalogue_as_of=datetime(2026, 8, 28, tzinfo=UTC),
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
    statement = lexical_trial_candidates_statement(
        query,
        candidate_limit=2,
        catalogue_as_of=datetime(2026, 8, 28, tzinfo=UTC),
    )
    compiled = statement.compile()

    rendered = str(compiled).lower()
    assert "ts_rank_cd" in rendered
    assert "trial_versions.raw_study" in rendered
    assert "trial_versions.ingested_at" in rendered
    assert "desc, trial_versions.nct_id" in rendered
    assert 2 in compiled.params.values()
    with pytest.raises(ValueError, match="candidate_limit must be positive"):
        lexical_trial_candidates_statement(
            PatientDerivedRetrievalQuery(),
            candidate_limit=0,
            catalogue_as_of=datetime(2026, 8, 28, tzinfo=UTC),
        )
