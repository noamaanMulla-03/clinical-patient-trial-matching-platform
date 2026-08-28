"""Acceptance checks for the committed, reproducible research baseline."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src.criteria.aggregation import _outcome_for
from src.db.models import Criterion, CriterionResult
from src.evaluation.runner import (
    compare_held_out_retrieval_dataset,
    evaluate_criteria_dataset,
    evaluate_parser_dataset,
    evaluate_retrieval_dataset,
    verify_frozen_dataset,
)

_FROZEN_DEMO = Path(__file__).parents[2] / "datasets/evaluation/frozen-demo"


@pytest.mark.evaluation
def test_frozen_demo_files_and_deterministic_configuration_are_unchanged() -> None:
    report = verify_frozen_dataset(_FROZEN_DEMO / "manifest.json")

    assert report["configuration"] == {
        "criterion_parser": "manual-v1",
        "model_configuration": "not-used-v1",
        "retrieval": "lexical-v1",
        "rule_engine": "deterministic-v1",
        "semantic_retrieval": "not-used-v1",
        "terminology_mapping": "source-coded-v1",
    }
    assert report["verified_files"] == [
        "criteria.json",
        "heldout-retrieval.json",
        "parser.json",
        "retrieval.json",
    ]


@pytest.mark.evaluation
def test_parser_baseline_is_measured_without_retrieval_or_matching() -> None:
    report = evaluate_parser_dataset(_FROZEN_DEMO / "parser.json")

    assert report["evaluation"] == "eligibility-parser"
    assert report["case_count"] == 5
    assert report["scope"] == (
        "eligibility text to atomic criteria only; excludes retrieval, patient facts, "
        "criterion evaluation, and final match aggregation"
    )
    assert report["metrics"] == {
        "source_span": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "atomic_rule": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "review_signal": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "abstention_accuracy": 1.0,
    }


@pytest.mark.evaluation
def test_lexical_baseline_is_measured_and_has_no_hidden_model_stage() -> None:
    report = evaluate_retrieval_dataset(_FROZEN_DEMO / "retrieval.json")

    assert report["configuration"]["retrieval"] == "lexical-v1"
    assert report["configuration"]["semantic_retrieval"] == "not-used-v1"
    assert report["configuration"]["reranking"] == "not-used-v1"
    assert report["topic_count"] == 2
    assert report["metrics"] == {
        "nDCG@5": pytest.approx(0.8366467312),
        "nDCG@10": pytest.approx(0.8366467312),
        "Precision@5": pytest.approx(0.3),
        "Precision@10": pytest.approx(0.15),
        "Recall@50": pytest.approx(0.75),
        "MRR": pytest.approx(1.0),
        "trec_grade_1_rate_top_10": pytest.approx(0.2916666667),
        "mean_latency_ms": pytest.approx(report["metrics"]["mean_latency_ms"]),
    }
    assert report["metrics"]["mean_latency_ms"] >= 0


@pytest.mark.evaluation
def test_hybrid_retrieval_regression_improves_ndcg_without_worsening_exclusions() -> (
    None
):
    report = compare_held_out_retrieval_dataset(_FROZEN_DEMO / "heldout-retrieval.json")

    assert report["acceptance"]["agreed_metric"] == "nDCG@5"
    assert report["acceptance"]["observed_improvement"] > 0
    assert "trec_grade_1_rate_top_10_delta" in report["acceptance"]
    assert report["acceptance"]["claimable"] is False
    assert report["acceptance"]["passed"] is False


@pytest.mark.evaluation
def test_annotated_criterion_baseline_measures_review_and_evidence_safety() -> None:
    report = evaluate_criteria_dataset(_FROZEN_DEMO / "criteria.json")

    assert report["case_count"] == 8
    assert report["metrics"] == {
        "criterion_macro_f1": 1.0,
        "exclusion_recall": 1.0,
        "false_clearance_rate": 0.0,
        "evidence_precision": 1.0,
        "abstention_rate": 0.5,
    }
    assert report["predicted_outcomes"] == {
        "conflicting": 1,
        "met": 2,
        "not_met": 2,
        "unknown": 3,
    }


@pytest.mark.evaluation
def test_acceptance_gate_keeps_review_outcomes_distinct() -> None:
    inclusion = Criterion(id=uuid4(), category="inclusion")
    exclusion = Criterion(id=uuid4(), category="exclusion")

    assert (
        _outcome_for(
            [inclusion], [_result(inclusion, outcome="met", requires_review=False)]
        )
        == "potential_match"
    )
    assert (
        _outcome_for(
            [exclusion], [_result(exclusion, outcome="not_met", requires_review=False)]
        )
        == "likely_excluded"
    )
    assert (
        _outcome_for(
            [inclusion], [_result(inclusion, outcome="unknown", requires_review=True)]
        )
        == "needs_review"
    )


def _result(
    criterion: Criterion, *, outcome: str, requires_review: bool
) -> CriterionResult:
    return CriterionResult(
        id=uuid4(),
        criterion_id=criterion.id,
        outcome=outcome,
        requires_review=requires_review,
    )
