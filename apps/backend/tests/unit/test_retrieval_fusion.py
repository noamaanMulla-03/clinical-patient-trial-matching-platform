"""Deterministic ranking checks for hybrid trial retrieval."""

import math

import pytest

from src.db.models import Trial
from src.retrieval.fusion import (
    RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
    RECIPROCAL_RANK_FUSION_VERSION,
    fuse_ranked_trial_candidates,
)
from src.retrieval.semantic import SemanticTrialCandidate


def _trial(nct_id: str) -> Trial:
    return Trial(nct_id=nct_id)


def test_reciprocal_rank_fusion_combines_positions_and_keeps_source_rationale() -> None:
    lexical_first = _trial("NCT00000001")
    lexical_second = _trial("NCT00000002")
    semantic_only = _trial("NCT00000003")

    fused = fuse_ranked_trial_candidates(
        [
            (lexical_first, {"lexical_score": 6.0, "matched_fact_ids": ["fact-1"]}),
            (lexical_second, {"lexical_score": 4.0, "matched_fact_ids": ["fact-2"]}),
        ],
        [
            SemanticTrialCandidate(trial=lexical_second, score=0.9, rank=1),
            SemanticTrialCandidate(trial=semantic_only, score=0.8, rank=2),
        ],
        candidate_limit=3,
    )

    assert [trial.nct_id for trial, _ in fused] == [
        "NCT00000002",
        "NCT00000001",
        "NCT00000003",
    ]
    first_scores = fused[0][1]
    assert first_scores["candidate_sources"] == ["lexical", "semantic"]
    assert first_scores["lexical_rank"] == 2
    assert first_scores["semantic_rank"] == 1
    assert first_scores["lexical_score"] == 4.0
    assert first_scores["semantic_score"] == 0.9
    assert first_scores["matched_fact_ids"] == ["fact-2"]
    assert first_scores["reciprocal_rank_fusion_rank"] == 1
    assert first_scores["reciprocal_rank_fusion_version"] == (
        RECIPROCAL_RANK_FUSION_VERSION
    )
    assert first_scores["reciprocal_rank_fusion_rank_constant"] == (
        RECIPROCAL_RANK_FUSION_RANK_CONSTANT
    )
    assert first_scores["reciprocal_rank_fusion_score"] == pytest.approx(
        1 / 62 + 1 / 61
    )
    assert fused[2][1]["candidate_sources"] == ["semantic"]
    assert fused[2][1]["reciprocal_rank_fusion_rank"] == 3


def test_reciprocal_rank_fusion_is_bounded_and_rejects_invalid_source_ranks() -> None:
    trial = _trial("NCT00000001")

    assert fuse_ranked_trial_candidates(
        [(trial, {"lexical_score": 1.0})],
        [],
        candidate_limit=1,
    )[0][1]["reciprocal_rank_fusion_score"] == pytest.approx(1 / 61)
    with pytest.raises(ValueError, match="candidate_limit must be positive"):
        fuse_ranked_trial_candidates([], [], candidate_limit=0)
    with pytest.raises(ValueError, match="semantic candidate rank must be positive"):
        fuse_ranked_trial_candidates(
            [],
            [SemanticTrialCandidate(trial=trial, score=math.nan, rank=0)],
            candidate_limit=1,
        )
