"""Regression checks for the versioned synthetic-FHIR evaluation fixture."""

from __future__ import annotations

from pathlib import Path

from src.evaluation.runner import verify_frozen_dataset
from src.evaluation.synthetic_fhir import evaluate_synthetic_fhir_benchmark

_DATASET = (
    Path(__file__).parents[2] / "datasets/evaluation/synthetic-fhir-v1/benchmark.json"
)


def test_synthetic_fhir_benchmark_exercises_normalization_retrieval_and_rules() -> None:
    report = evaluate_synthetic_fhir_benchmark(_DATASET)

    assert report["claimable"] is False
    assert report["case_count"] == 3
    assert report["metrics"] == {
        "expected_candidate_recall_at_review_limit": 1.0,
        "outcome_macro_f1": 1.0,
        "exclusion_recall": 1.0,
        "false_clearance_rate": 0.0,
        "evidence_presence_rate": 1.0,
        "needs_review_rate": 0.5,
    }
    assert report["cases"][0]["trial_results"] == [
        {
            "nct_id": "NCT91000001",
            "outcome": "potential_match",
            "criterion_outcomes": ["met", "met"],
        },
        {
            "nct_id": "NCT91000002",
            "outcome": "likely_excluded",
            "criterion_outcomes": ["not_met"],
        },
    ]


def test_synthetic_fhir_benchmark_fixture_is_frozen() -> None:
    report = verify_frozen_dataset(_DATASET.with_name("manifest.json"))

    assert report["verified_files"] == ["benchmark.json"]
