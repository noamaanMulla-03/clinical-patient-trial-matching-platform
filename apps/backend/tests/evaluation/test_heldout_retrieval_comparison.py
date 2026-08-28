"""Held-out hybrid retrieval comparison checks."""

import json
from pathlib import Path

import pytest

from src.evaluation.runner import (
    EvaluationDatasetError,
    compare_held_out_retrieval_dataset,
)
from src.retrieval.fusion import (
    RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
    RECIPROCAL_RANK_FUSION_VERSION,
)
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


def test_heldout_comparison_uses_production_rrf_and_reports_metric_delta(
    tmp_path: Path,
) -> None:
    dataset = _write_dataset(tmp_path)

    report = compare_held_out_retrieval_dataset(dataset)

    assert report["evaluation"] == "held-out-trec-retrieval-comparison"
    assert report["topic_count"] == 1
    assert report["lexical_only"]["topics"][0]["ranked_nct_ids"] == [
        "NCT00000001",
        "NCT00000002",
    ]
    assert report["hybrid_rrf"]["topics"][0]["ranked_nct_ids"] == [
        "NCT00000002",
        "NCT00000001",
        "NCT00000003",
    ]
    assert report["delta_hybrid_minus_lexical"]["nDCG@5"] > 0
    assert report["acceptance"] == {
        "benchmark_kind": "synthetic_regression",
        "claimable": False,
        "agreed_metric": "nDCG@5",
        "minimum_improvement": 0.01,
        "observed_improvement": pytest.approx(
            report["delta_hybrid_minus_lexical"]["nDCG@5"]
        ),
        "maximum_excluded_trial_rate_top_10_increase": 0.0,
        "observed_excluded_trial_rate_top_10_increase": pytest.approx(
            report["delta_hybrid_minus_lexical"]["excluded_trial_rate_top_10"]
        ),
        "passed": False,
    }
    assert report["latency_scope"] == (
        "ranking only; precomputed semantic lists exclude model encoding time"
    )


def test_heldout_comparison_rejects_unreviewed_or_nonheldout_input(
    tmp_path: Path,
) -> None:
    payload = _dataset_payload()
    payload["topics"][0]["split"] = "training"
    dataset = tmp_path / "invalid-split.json"
    dataset.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationDatasetError, match="explicitly marked heldout"):
        compare_held_out_retrieval_dataset(dataset)

    payload = _dataset_payload()
    del payload["configuration"]["term_mapping_version"]
    dataset.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationDatasetError, match="reviewed term_mapping_version"):
        compare_held_out_retrieval_dataset(dataset)

    payload = _dataset_payload()
    payload["configuration"]["benchmark"] = {"kind": "trec_clinical_trials"}
    dataset.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationDatasetError, match="supported source year"):
        compare_held_out_retrieval_dataset(dataset)


def _write_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "heldout-retrieval.json"
    dataset.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    return dataset


def _dataset_payload() -> dict[str, object]:
    return {
        "candidate_limit": 3,
        "configuration": {
            "term_mapping_version": "trec-review-map-v1",
            "benchmark": {"kind": "synthetic_regression"},
            "semantic_model": SEMANTIC_EMBEDDING_MODEL.snapshot(),
            "rank_fusion": {
                "method": RECIPROCAL_RANK_FUSION_VERSION,
                "rank_constant": RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
            },
            "acceptance_gate": {
                "agreed_metric": "nDCG@5",
                "minimum_improvement": 0.01,
                "maximum_excluded_trial_rate_top_10_increase": 0.0,
            },
        },
        "trials": [
            {"nct_id": "NCT00000001", "conditions": ["Melanoma"]},
            {"nct_id": "NCT00000002", "conditions": ["Melanoma"]},
            {"nct_id": "NCT00000003", "conditions": ["Other"]},
        ],
        "topics": [
            {
                "id": "2022-001",
                "split": "heldout",
                "terms": [
                    {
                        "text": "Melanoma",
                        "source_fact_id": "reviewed-fact-1",
                        "kind": "condition",
                    }
                ],
                "semantic_ranked_nct_ids": ["NCT00000002", "NCT00000003"],
                "judgments": {
                    "NCT00000001": 1,
                    "NCT00000002": 2,
                    "NCT00000003": 0,
                },
            }
        ],
    }
