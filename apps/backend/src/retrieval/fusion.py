"""Deterministic reciprocal-rank fusion for bounded trial candidate lists."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.retrieval.semantic import SemanticTrialCandidate
from src.retrieval.trial_documents import SearchableTrial

RECIPROCAL_RANK_FUSION_VERSION = "reciprocal-rank-fusion-v1"
RECIPROCAL_RANK_FUSION_RANK_CONSTANT = 60


@dataclass(slots=True)
class _FusedCandidate[SearchableTrialType: SearchableTrial]:
    trial: SearchableTrialType
    scores: dict[str, Any]
    lexical_rank: int | None = None
    semantic_rank: int | None = None


def fuse_ranked_trial_candidates[SearchableTrialType: SearchableTrial](
    ranked_lexical_trials: Sequence[tuple[SearchableTrialType, dict[str, Any]]],
    semantic_candidates: Sequence[SemanticTrialCandidate[SearchableTrialType]],
    *,
    candidate_limit: int,
) -> list[tuple[SearchableTrialType, dict[str, Any]]]:
    """Fuse two bounded retrieval rankings without changing review outcomes.

    Reciprocal rank fusion compares rank positions rather than mixing the
    incomparable lexical field score and cosine-similarity score. The fixed
    constant and source ranks remain with every persisted candidate so a
    reviewer can trace why a public trial appears where it does.
    """
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")

    candidates: dict[str, _FusedCandidate[SearchableTrialType]] = {}
    for lexical_rank, (trial, lexical_scores) in enumerate(ranked_lexical_trials, 1):
        candidate = _candidate_for(candidates, trial)
        candidate.lexical_rank = lexical_rank
        candidate.scores.update(lexical_scores)
        candidate.scores.update(
            {
                "candidate_sources": ["lexical"],
                "lexical_rank": lexical_rank,
            }
        )

    for semantic_candidate in semantic_candidates:
        if semantic_candidate.rank < 1:
            raise ValueError("semantic candidate rank must be positive.")
        candidate = _candidate_for(candidates, semantic_candidate.trial)
        candidate.semantic_rank = semantic_candidate.rank
        candidate.scores.update(
            {
                "semantic_score": semantic_candidate.score,
                "semantic_rank": semantic_candidate.rank,
            }
        )
        candidate.scores["candidate_sources"] = (
            ["lexical", "semantic"]
            if candidate.lexical_rank is not None
            else ["semantic"]
        )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (-_fusion_score(candidate), candidate.trial.nct_id),
    )[:candidate_limit]
    return [
        (
            candidate.trial,
            {
                **candidate.scores,
                "reciprocal_rank_fusion_score": _fusion_score(candidate),
                "reciprocal_rank_fusion_rank": rank,
                "reciprocal_rank_fusion_rank_constant": (
                    RECIPROCAL_RANK_FUSION_RANK_CONSTANT
                ),
                "reciprocal_rank_fusion_version": RECIPROCAL_RANK_FUSION_VERSION,
            },
        )
        for rank, candidate in enumerate(ranked, 1)
    ]


def _candidate_for[SearchableTrialType: SearchableTrial](
    candidates: dict[str, _FusedCandidate[SearchableTrialType]],
    trial: SearchableTrialType,
) -> _FusedCandidate[SearchableTrialType]:
    if candidate := candidates.get(trial.nct_id):
        return candidate
    candidate = _FusedCandidate(trial=trial, scores={})
    candidates[trial.nct_id] = candidate
    return candidate


def _fusion_score[SearchableTrialType: SearchableTrial](
    candidate: _FusedCandidate[SearchableTrialType],
) -> float:
    return sum(
        1 / (RECIPROCAL_RANK_FUSION_RANK_CONSTANT + rank)
        for rank in (candidate.lexical_rank, candidate.semantic_rank)
        if rank is not None
    )
