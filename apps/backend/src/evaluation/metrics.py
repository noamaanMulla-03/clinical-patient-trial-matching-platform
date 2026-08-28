"""Small, dependency-free metrics used by the reproducible baseline commands."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from math import log2


def mean(values: Iterable[float]) -> float:
    """Return a stable zero for an empty collection of metric values."""
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def ndcg_at_k(
    relevances: Sequence[int], *, ideal_relevances: Iterable[int], k: int
) -> float:
    """Compute graded nDCG using TREC's 0/1/2 judgments."""
    return (
        _dcg(relevances[:k]) / _dcg(sorted(ideal_relevances, reverse=True)[:k])
        if _dcg(sorted(ideal_relevances, reverse=True)[:k])
        else 0.0
    )


def precision_at_k(relevances: Sequence[int], *, k: int) -> float:
    """Treat TREC judgment 2 (eligible) as relevant for precision and recall."""
    return sum(relevance == 2 for relevance in relevances[:k]) / k


def recall_at_k(relevances: Sequence[int], *, total_relevant: int, k: int) -> float:
    """Return zero where a topic has no judged eligible trials."""
    if total_relevant == 0:
        return 0.0
    return sum(relevance == 2 for relevance in relevances[:k]) / total_relevant


def reciprocal_rank(relevances: Sequence[int]) -> float:
    """Return the reciprocal rank of the first judged eligible trial."""
    for position, relevance in enumerate(relevances, start=1):
        if relevance == 2:
            return 1.0 / position
    return 0.0


def trec_grade_1_rate_at_k(relevances: Sequence[int], *, k: int) -> float:
    """Measure TREC grade 1, not a clinical exclusion or safety rate."""
    top_k = relevances[:k]
    return sum(relevance == 1 for relevance in top_k) / len(top_k) if top_k else 0.0


def macro_f1(expected: Sequence[str], predicted: Sequence[str]) -> float:
    """Macro F1 over every expected outcome label in a frozen test set."""
    labels = sorted(set(expected))
    scores: list[float] = []
    for label in labels:
        true_positive = sum(
            expected_value == label and predicted_value == label
            for expected_value, predicted_value in zip(expected, predicted, strict=True)
        )
        false_positive = sum(
            expected_value != label and predicted_value == label
            for expected_value, predicted_value in zip(expected, predicted, strict=True)
        )
        false_negative = sum(
            expected_value == label and predicted_value != label
            for expected_value, predicted_value in zip(expected, predicted, strict=True)
        )
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append((2 * true_positive / denominator) if denominator else 0.0)
    return mean(scores)


def label_recall(
    expected: Sequence[str], predicted: Sequence[str], *, label: str
) -> float:
    """Recall for a single expected outcome label."""
    positives = sum(value == label for value in expected)
    if positives == 0:
        return 0.0
    return (
        sum(
            expected_value == label and predicted_value == label
            for expected_value, predicted_value in zip(expected, predicted, strict=True)
        )
        / positives
    )


def outcome_counts(values: Iterable[str]) -> dict[str, int]:
    """Return sorted outcome counts for a human-readable baseline report."""
    return dict(sorted(Counter(values).items()))


def _dcg(relevances: Sequence[int]) -> float:
    return float(
        sum(
            (2**relevance - 1) / log2(position + 1)
            for position, relevance in enumerate(relevances, start=1)
        )
    )
