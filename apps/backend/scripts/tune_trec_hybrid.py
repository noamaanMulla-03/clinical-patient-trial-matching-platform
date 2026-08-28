"""Tune weighted reciprocal-rank fusion on a fixed public TREC split only."""

from __future__ import annotations

import json
from pathlib import Path

from src.evaluation.metrics import (
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    trec_grade_1_rate_at_k,
)
from src.retrieval.fusion import RECIPROCAL_RANK_FUSION_RANK_CONSTANT

_SEMANTIC_WEIGHT_CANDIDATES = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0, 8.0)
_CANDIDATE_LIMIT = 100
_TUNING_TOPIC_IDS = frozenset(str(value) for value in range(1, 51, 2))
_HELD_OUT_TOPIC_IDS = frozenset(str(value) for value in range(2, 51, 2))


def main() -> int:
    base = Path("datasets/evaluation/trec")
    lexical = json.loads((base / "results/2022-token-lexical.json").read_text())
    semantic = json.loads((base / "results/2022-semantic-hybrid-full.json").read_text())
    structured_report = json.loads(
        (
            base / "results/2022-semantic-hybrid-structured-reranker-full.json"
        ).read_text()
    )
    qrels = _qrels(base / "raw/qrels-2022.txt")
    lexical_ranks = _ranks_by_topic(lexical["topics"])
    semantic_ranks = _ranks_by_topic(semantic["semantic"]["topics"])
    hybrid_ranks = _ranks_by_topic(structured_report["hybrid"]["topics"])
    reranked_ranks = _ranks_by_topic(
        structured_report["hybrid_structured_reranker"]["topics"]
    )
    topic_ids = tuple(sorted(qrels, key=int))
    _validate_topics(
        topic_ids,
        lexical_ranks,
        semantic_ranks,
        hybrid_ranks,
        reranked_ranks,
    )

    configurations = []
    for semantic_weight in _SEMANTIC_WEIGHT_CANDIDATES:
        ranked = {
            topic_id: _fuse(
                lexical_ranks[topic_id],
                semantic_ranks[topic_id],
                lexical_weight=1.0,
                semantic_weight=semantic_weight,
            )
            for topic_id in topic_ids
        }
        configurations.append(
            {
                "lexical_weight": 1.0,
                "semantic_weight": semantic_weight,
                "tuning": _metrics_for_topics(
                    ranked, qrels=qrels, topic_ids=_TUNING_TOPIC_IDS
                ),
                "heldout": _metrics_for_topics(
                    ranked, qrels=qrels, topic_ids=_HELD_OUT_TOPIC_IDS
                ),
            }
        )

    selected = _selected_configuration(configurations)
    output = {
        "evaluation": "trec-2022-weighted-hybrid-tuning",
        "claimable": False,
        "scope": (
            "public TREC rankings only; no patient record, FHIR content, or "
            "clinical eligibility outcome is created"
        ),
        "rank_fusion": {
            "method": "weighted-reciprocal-rank-fusion",
            "rank_constant": RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
        },
        "topic_split": {
            "tuning_topic_ids": sorted(_TUNING_TOPIC_IDS, key=int),
            "heldout_topic_ids": sorted(_HELD_OUT_TOPIC_IDS, key=int),
        },
        "selection_protocol": {
            "selected_on": "tuning_topic_ids_only",
            "heldout_role": "reporting_only; not used for configuration selection",
            "clinical_release_gate": "not-supported-by-trec-grade-composition",
        },
        "candidate_profiles": _candidate_profiles(
            qrels=qrels,
            lexical_ranks=lexical_ranks,
            semantic_ranks=semantic_ranks,
            hybrid_ranks=hybrid_ranks,
            reranked_ranks=reranked_ranks,
        ),
        "configurations": configurations,
        "selected_configuration": selected,
    }
    output_path = base / "results/2022-weighted-hybrid-tuning.json"
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "completed", "output": str(output_path)}))
    return 0


def _qrels(path: Path) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines():
        topic_id, _, nct_id, grade = line.split()
        rows.setdefault(topic_id, {})[nct_id] = int(grade)
    return rows


def _ranks_by_topic(topics: object) -> dict[str, list[str]]:
    if not isinstance(topics, list):
        raise ValueError("TREC comparison output must contain ranked topics.")
    ranks: dict[str, list[str]] = {}
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("TREC comparison output has an invalid topic.")
        topic_id = topic.get("topic_id")
        identifiers = topic.get("ranked_nct_ids")
        if not isinstance(topic_id, str) or not isinstance(identifiers, list):
            raise ValueError("TREC comparison output is missing bounded ranks.")
        if not all(isinstance(identifier, str) for identifier in identifiers):
            raise ValueError("TREC comparison output has an invalid trial identifier.")
        ranks[topic_id] = identifiers
    return ranks


def _validate_topics(
    topic_ids: tuple[str, ...],
    *rank_sets: dict[str, list[str]],
) -> None:
    if set(topic_ids) != _TUNING_TOPIC_IDS | _HELD_OUT_TOPIC_IDS:
        raise ValueError("TREC 2022 topic split no longer matches the fixed protocol.")
    for topic_id in topic_ids:
        if any(topic_id not in ranks for ranks in rank_sets):
            raise ValueError(f"TREC topic {topic_id} is missing a ranked source list.")


def _candidate_profiles(
    *,
    qrels: dict[str, dict[str, int]],
    lexical_ranks: dict[str, list[str]],
    semantic_ranks: dict[str, list[str]],
    hybrid_ranks: dict[str, list[str]],
    reranked_ranks: dict[str, list[str]],
) -> list[dict[str, object]]:
    """Report fixed configurations separately from tuning weight experiments."""
    return [
        {
            "name": name,
            "tuning": _metrics_for_topics(
                ranks, qrels=qrels, topic_ids=_TUNING_TOPIC_IDS
            ),
            "heldout": _metrics_for_topics(
                ranks, qrels=qrels, topic_ids=_HELD_OUT_TOPIC_IDS
            ),
        }
        for name, ranks in (
            ("lexical_only", lexical_ranks),
            ("semantic_only", semantic_ranks),
            ("hybrid_equal_rrf", hybrid_ranks),
            ("hybrid_equal_rrf_structured_reranker", reranked_ranks),
        )
    ]


def _fuse(
    lexical: list[str],
    semantic: list[str],
    *,
    lexical_weight: float,
    semantic_weight: float,
) -> list[str]:
    if lexical_weight <= 0 or semantic_weight <= 0:
        raise ValueError("Fusion weights must be positive.")
    scores: dict[str, float] = {}
    for ranked, weight in ((lexical, lexical_weight), (semantic, semantic_weight)):
        for rank, nct_id in enumerate(ranked, 1):
            scores[nct_id] = scores.get(nct_id, 0.0) + weight / (
                RECIPROCAL_RANK_FUSION_RANK_CONSTANT + rank
            )
    return [
        nct_id
        for nct_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            :_CANDIDATE_LIMIT
        ]
    ]


def _metrics_for_topics(
    ranked: dict[str, list[str]],
    *,
    qrels: dict[str, dict[str, int]],
    topic_ids: frozenset[str],
) -> dict[str, float]:
    results = [
        _topic_metrics(ranked[topic_id], qrels[topic_id]) for topic_id in topic_ids
    ]
    names = (
        "nDCG@5",
        "nDCG@10",
        "Precision@5",
        "Precision@10",
        "Recall@50",
        "MRR",
        "trec_grade_1_rate_top_10",
    )
    return {name: mean(float(result[name]) for result in results) for name in names}


def _topic_metrics(ranked: list[str], judgments: dict[str, int]) -> dict[str, float]:
    grades = [judgments.get(nct_id, 0) for nct_id in ranked]
    ideal = list(judgments.values())
    relevant = sum(grade == 2 for grade in ideal)
    return {
        "nDCG@5": ndcg_at_k(grades, ideal_relevances=ideal, k=5),
        "nDCG@10": ndcg_at_k(grades, ideal_relevances=ideal, k=10),
        "Precision@5": precision_at_k(grades, k=5),
        "Precision@10": precision_at_k(grades, k=10),
        "Recall@50": recall_at_k(grades, total_relevant=relevant, k=50),
        "MRR": reciprocal_rank(grades),
        "trec_grade_1_rate_top_10": trec_grade_1_rate_at_k(grades, k=10),
    }


def _selected_configuration(
    configurations: list[dict[str, object]],
) -> dict[str, object] | None:
    if not configurations:
        return None
    return max(
        configurations,
        key=lambda configuration: (
            float(configuration["tuning"]["nDCG@10"]),  # type: ignore[index]
            float(configuration["tuning"]["Precision@10"]),  # type: ignore[index]
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
