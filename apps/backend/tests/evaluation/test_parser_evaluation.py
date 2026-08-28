"""Eligibility-parser evaluation stays isolated from later workflow stages."""

import json
from pathlib import Path

import pytest

from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.evaluation.runner import EvaluationDatasetError, evaluate_parser_dataset


def test_parser_evaluation_rejects_a_dataset_with_a_different_parser_version(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "parser.json"
    dataset.write_text(
        json.dumps(
            {
                "configuration": {"parser": {"parser_version": "changed"}},
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDatasetError, match="pinned parser configuration"):
        evaluate_parser_dataset(dataset)


def test_parser_evaluation_reports_only_parser_scoped_metrics(tmp_path: Path) -> None:
    dataset = tmp_path / "parser.json"
    dataset.write_text(
        json.dumps(
            {
                "configuration": {
                    "parser": ELIGIBILITY_PARSER_CONFIGURATION.snapshot()
                },
                "cases": [
                    {
                        "id": "abstention",
                        "eligibility_text": "Inclusion Criteria:\n- ECOG status 0-1",
                        "expected": {
                            "criteria": [],
                            "review_signals": [],
                            "expect_abstention": True,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_parser_dataset(dataset)

    assert set(report) == {
        "evaluation",
        "scope",
        "dataset",
        "configuration",
        "case_count",
        "metrics",
        "cases",
    }
    assert report["metrics"]["abstention_accuracy"] == 1.0
