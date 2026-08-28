"""Checks for the public-ID-only TREC retrieval diagnosis report."""

from __future__ import annotations

from scripts.diagnose_trec_retrieval import diagnose_topics


def test_diagnosis_surfaces_high_excluded_rate_and_lower_semantic_quality() -> None:
    report = diagnose_topics(
        [
            {
                "topic_id": "1",
                "nDCG@10": 0.4,
                "excluded_trial_rate_top_10": 0.0,
            },
            {
                "topic_id": "2",
                "nDCG@10": 0.2,
                "excluded_trial_rate_top_10": 0.1,
            },
        ],
        [
            {
                "topic_id": "1",
                "nDCG@10": 0.3,
                "excluded_trial_rate_top_10": 0.4,
                "ranked_nct_ids": ["NCT00000001"],
            },
            {
                "topic_id": "2",
                "nDCG@10": 0.4,
                "excluded_trial_rate_top_10": 0.2,
                "ranked_nct_ids": ["NCT00000002"],
            },
        ],
    )

    assert report["topic_count"] == 2
    assert report["highest_semantic_excluded_rate"][0]["topic_id"] == "1"
    assert report["semantic_worse_than_lexical_nDCG@10"] == [
        {
            "topic_id": "1",
            "lexical_nDCG@10": 0.4,
            "semantic_nDCG@10": 0.3,
            "lexical_excluded_trial_rate_top_10": 0.0,
            "semantic_excluded_trial_rate_top_10": 0.4,
            "semantic_ranked_nct_ids": ["NCT00000001"],
        }
    ]
