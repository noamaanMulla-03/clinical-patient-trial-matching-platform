"""Read-only tests for the TREC lexical evaluation adapter."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from src.evaluation.trec import TrecEvaluationError, evaluate_trec_lexical_baseline


def test_trec_lexical_baseline_scores_archived_public_trials_without_persistence(
    tmp_path: Path,
) -> None:
    topics = tmp_path / "topics.xml"
    topics.write_text(
        '<topics><topic number="1">Melanoma patient</topic></topics>',
        encoding="utf-8",
    )
    qrels = tmp_path / "qrels.txt"
    qrels.write_text("1 0 NCT00000001 2\n1 0 NCT00000002 1\n", encoding="utf-8")
    archive = _write_archive(tmp_path)

    report = evaluate_trec_lexical_baseline(
        topics_path=topics,
        qrels_path=qrels,
        archives=[archive],
        candidate_limit=2,
    )

    assert report["evaluation"] == "trec-token-adapter-lexical-baseline"
    assert report["claimable"] is False
    assert report["trial_count"] == 2
    assert report["topic_count"] == 1
    assert report["topics"] == [
        {
            "topic_id": "1",
            "ranked_nct_ids": ["NCT00000001", "NCT00000002"],
            "nDCG@5": 1.0,
            "nDCG@10": 1.0,
            "Precision@5": 0.2,
            "Precision@10": 0.1,
            "Recall@50": 1.0,
            "MRR": 1.0,
            "excluded_trial_rate_top_10": 0.5,
        }
    ]


def test_trec_lexical_baseline_rejects_a_topic_without_official_judgments(
    tmp_path: Path,
) -> None:
    topics = tmp_path / "topics.xml"
    topics.write_text(
        '<topics><topic number="1">Melanoma patient</topic></topics>',
        encoding="utf-8",
    )
    qrels = tmp_path / "qrels.txt"
    qrels.write_text("2 0 NCT00000001 2\n", encoding="utf-8")

    with pytest.raises(TrecEvaluationError, match="has no official judgments"):
        evaluate_trec_lexical_baseline(
            topics_path=topics,
            qrels_path=qrels,
            archives=[_write_archive(tmp_path)],
        )


def test_trec_lexical_baseline_limits_the_scanned_public_trial_slice(
    tmp_path: Path,
) -> None:
    topics = tmp_path / "topics.xml"
    topics.write_text(
        '<topics><topic number="1">Melanoma patient</topic></topics>',
        encoding="utf-8",
    )
    qrels = tmp_path / "qrels.txt"
    qrels.write_text("1 0 NCT00000001 2\n1 0 NCT00000002 1\n", encoding="utf-8")

    report = evaluate_trec_lexical_baseline(
        topics_path=topics,
        qrels_path=qrels,
        archives=[_write_archive(tmp_path)],
        trial_limit=1,
    )

    assert report["trial_count"] == 1
    assert report["topics"][0]["ranked_nct_ids"] == ["NCT00000001"]


def _write_archive(tmp_path: Path) -> Path:
    archive_path = tmp_path / "trials.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "NCT00000001.xml",
            """
            <clinical_study>
              <id_info><nct_id>NCT00000001</nct_id></id_info>
              <brief_title>Unrelated title</brief_title>
              <condition>Melanoma</condition>
            </clinical_study>
            """,
        )
        archive.writestr(
            "NCT00000002.xml",
            """
            <clinical_study>
              <id_info><nct_id>NCT00000002</nct_id></id_info>
              <brief_title>Melanoma study</brief_title>
            </clinical_study>
            """,
        )
    return archive_path
