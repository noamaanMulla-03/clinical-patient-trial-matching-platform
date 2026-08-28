"""Checks for the fixed public TREC weighted-fusion tuning protocol."""

from scripts.tune_trec_hybrid import _candidate_profiles, _fuse, _passes_acceptance


def test_weighted_fusion_can_prioritize_the_semantic_rank_list() -> None:
    assert _fuse(
        ["NCT00000001", "NCT00000002"],
        ["NCT00000003", "NCT00000004"],
        lexical_weight=1.0,
        semantic_weight=4.0,
    )[:2] == ["NCT00000003", "NCT00000004"]


def test_acceptance_requires_relevance_and_low_excluded_rate() -> None:
    assert _passes_acceptance(
        {
            "nDCG@10": 0.25,
            "Precision@10": 0.25,
            "excluded_trial_rate_top_10": 0.05,
        }
    )
    assert not _passes_acceptance(
        {
            "nDCG@10": 0.30,
            "Precision@10": 0.30,
            "excluded_trial_rate_top_10": 0.051,
        }
    )


def test_candidate_profiles_keep_re_ranker_as_a_separate_fixed_configuration() -> None:
    ranks = {str(topic_id): ["NCT00000001"] for topic_id in range(1, 51)}
    qrels = {str(topic_id): {"NCT00000001": 2} for topic_id in range(1, 51)}

    profiles = _candidate_profiles(
        qrels=qrels,
        lexical_ranks=ranks,
        semantic_ranks=ranks,
        hybrid_ranks=ranks,
        reranked_ranks=ranks,
    )

    assert [profile["name"] for profile in profiles] == [
        "lexical_only",
        "semantic_only",
        "hybrid_equal_rrf",
        "hybrid_equal_rrf_structured_reranker",
    ]
    assert all(profile["heldout"]["nDCG@10"] == 1.0 for profile in profiles)
