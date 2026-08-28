"""Read-only lexical evaluation over the official TREC Clinical Trials files.

TREC topics are synthetic free-text cases, not product FHIR inputs.  This
module never persists a topic or trial record and exposes a deterministic
token adapter solely to measure a lexical retrieval baseline.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as element_tree
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from src.evaluation.metrics import (
    excluded_rate_at_k,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9-]{2,}", flags=re.IGNORECASE)
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "before",
        "being",
        "between",
        "because",
        "could",
        "during",
        "from",
        "have",
        "into",
        "patient",
        "patients",
        "should",
        "that",
        "their",
        "there",
        "these",
        "they",
        "this",
        "through",
        "with",
        "without",
        "would",
    }
)
_FIELD_WEIGHTS = {
    "title": 3.0,
    "conditions": 4.0,
    "interventions": 2.0,
    "eligibility": 1.0,
}


class TrecEvaluationError(ValueError):
    """Raised when a required official TREC source cannot be read safely."""


@dataclass(frozen=True, slots=True)
class _Topic:
    identifier: str
    terms: frozenset[str]


@dataclass(frozen=True, slots=True)
class _TrialFields:
    nct_id: str
    searchable: dict[str, set[str]]


def evaluate_trec_lexical_baseline(
    *,
    topics_path: Path,
    qrels_path: Path,
    archives: Sequence[Path],
    candidate_limit: int = 100,
    topic_limit: int | None = None,
    trial_limit: int | None = None,
) -> dict[str, object]:
    """Measure deterministic token-overlap retrieval without product persistence.

    The adapter deliberately maps words, not inferred diagnoses or other facts.
    It is therefore an engineering benchmark and cannot claim FHIR workflow or
    clinical performance.
    """
    if candidate_limit < 1:
        raise TrecEvaluationError("candidate_limit must be positive.")
    if topic_limit is not None and topic_limit < 1:
        raise TrecEvaluationError("topic_limit must be positive when supplied.")
    if trial_limit is not None and trial_limit < 1:
        raise TrecEvaluationError("trial_limit must be positive when supplied.")
    topics = _read_topics(topics_path)
    if topic_limit is not None:
        topics = topics[:topic_limit]
    if not topics:
        raise TrecEvaluationError("No TREC topics were available for evaluation.")
    judgments = _read_qrels(qrels_path)
    for topic in topics:
        if topic.identifier not in judgments:
            raise TrecEvaluationError(
                f"TREC topic {topic.identifier} has no official judgments."
            )

    ranked_candidates: list[list[tuple[str, float]]] = [[] for _ in topics]
    terms_to_topics: dict[str, list[int]] = defaultdict(list)
    for index, topic in enumerate(topics):
        for term in topic.terms:
            terms_to_topics[term].append(index)

    started_at = time.perf_counter()
    trial_count = 0
    for fields in _iter_trial_fields(archives):
        if trial_limit is not None and trial_count >= trial_limit:
            break
        trial_count += 1
        trial_scores: dict[int, float] = defaultdict(float)
        for field_name, field_terms in fields.searchable.items():
            weight = _FIELD_WEIGHTS[field_name]
            for term in field_terms:
                for topic_index in terms_to_topics.get(term, ()):
                    trial_scores[topic_index] += weight
        for topic_index, score in trial_scores.items():
            _retain_candidate(
                ranked_candidates[topic_index],
                nct_id=fields.nct_id,
                score=score,
                candidate_limit=candidate_limit,
            )
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    results: list[dict[str, object]] = []
    for index, topic in enumerate(topics):
        ranked_ids = [
            nct_id
            for nct_id, _ in sorted(
                ranked_candidates[index], key=lambda item: (-item[1], item[0])
            )
        ]
        topic_judgments = judgments[topic.identifier]
        relevances = [topic_judgments.get(nct_id, 0) for nct_id in ranked_ids]
        ideal_relevances = list(topic_judgments.values())
        eligible_count = sum(value == 2 for value in ideal_relevances)
        results.append(
            {
                "topic_id": topic.identifier,
                "ranked_nct_ids": ranked_ids,
                "nDCG@5": ndcg_at_k(relevances, ideal_relevances=ideal_relevances, k=5),
                "nDCG@10": ndcg_at_k(
                    relevances, ideal_relevances=ideal_relevances, k=10
                ),
                "Precision@5": precision_at_k(relevances, k=5),
                "Precision@10": precision_at_k(relevances, k=10),
                "Recall@50": recall_at_k(
                    relevances, total_relevant=eligible_count, k=50
                ),
                "MRR": reciprocal_rank(relevances),
                "excluded_trial_rate_top_10": excluded_rate_at_k(relevances, k=10),
            }
        )
    metric_names = (
        "nDCG@5",
        "nDCG@10",
        "Precision@5",
        "Precision@10",
        "Recall@50",
        "MRR",
        "excluded_trial_rate_top_10",
    )
    return {
        "evaluation": "trec-token-adapter-lexical-baseline",
        "claimable": False,
        "claim_scope": (
            "engineering-only token adapter; does not evaluate FHIR import, "
            "clinical fact extraction, eligibility, or enrollment"
        ),
        "topic_count": len(results),
        "trial_count": trial_count,
        "candidate_limit": candidate_limit,
        "latency_scope": "whole-corpus token-index scan; excludes source download",
        "metrics": {
            key: mean(_metric_value(result, key) for result in results)
            for key in metric_names
        }
        | {"total_scan_latency_ms": elapsed_ms},
        "topics": results,
    }


def _read_topics(path: Path) -> list[_Topic]:
    try:
        root = element_tree.parse(path).getroot()
    except (OSError, element_tree.ParseError) as error:
        raise TrecEvaluationError(
            "Could not read the official TREC topics XML."
        ) from error
    topics: list[_Topic] = []
    for node in root.findall(".//topic"):
        identifier = node.get("number")
        if not identifier:
            raise TrecEvaluationError("A TREC topic is missing its number.")
        terms = _tokens(" ".join(node.itertext()))
        if not terms:
            raise TrecEvaluationError(f"TREC topic {identifier} has no usable terms.")
        topics.append(_Topic(identifier=identifier, terms=frozenset(terms)))
    return topics


def _read_qrels(path: Path) -> dict[str, dict[str, int]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise TrecEvaluationError("Could not read official TREC qrels.") from error
    judgments: dict[str, dict[str, int]] = defaultdict(dict)
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            raise TrecEvaluationError("Official TREC qrels contains an invalid row.")
        topic_id, _, nct_id, grade_text = parts
        try:
            grade = int(grade_text)
        except ValueError as error:
            raise TrecEvaluationError(
                "Official TREC qrels has an invalid grade."
            ) from error
        if grade not in {0, 1, 2}:
            raise TrecEvaluationError("Official TREC qrels grades must be 0, 1, or 2.")
        judgments[topic_id][nct_id] = grade
    return dict(judgments)


def _iter_trial_fields(archives: Sequence[Path]) -> Iterable[_TrialFields]:
    if not archives:
        raise TrecEvaluationError("At least one historical TREC archive is required.")
    for archive_path in archives:
        try:
            with ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    if not member.filename.endswith(".xml"):
                        continue
                    yield _trial_fields(archive.read(member))
        except (BadZipFile, OSError) as error:
            raise TrecEvaluationError("Could not read a TREC trial archive.") from error


def _trial_fields(source: bytes) -> _TrialFields:
    try:
        root = element_tree.fromstring(source)
    except element_tree.ParseError as error:
        raise TrecEvaluationError(
            "A historical TREC trial XML record is invalid."
        ) from error
    nct_id = root.findtext("id_info/nct_id")
    if not nct_id:
        raise TrecEvaluationError("A historical TREC trial is missing its NCT ID.")
    interventions = " ".join(
        value
        for intervention in root.findall("intervention")
        for value in (
            intervention.findtext("intervention_name"),
            intervention.findtext("description"),
        )
        if value
    )
    return _TrialFields(
        nct_id=nct_id,
        searchable={
            "title": _tokens(root.findtext("brief_title") or ""),
            "conditions": _tokens(
                " ".join(
                    value
                    for condition in root.findall("condition")
                    if (value := condition.text)
                )
            ),
            "interventions": _tokens(interventions),
            "eligibility": _tokens(
                root.findtext("eligibility/criteria/textblock") or ""
            ),
        },
    )


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_PATTERN.findall(value)
        if token.casefold() not in _STOP_WORDS
    }


def tokenize_trec_text(value: str) -> set[str]:
    """Return the bounded token adapter used only by read-only TREC evaluation."""
    return _tokens(value)


def _retain_candidate(
    candidates: list[tuple[str, float]],
    *,
    nct_id: str,
    score: float,
    candidate_limit: int,
) -> None:
    """Keep a bounded exact top-k list for one topic while streaming the corpus."""
    if len(candidates) < candidate_limit:
        candidates.append((nct_id, score))
        return
    lowest_score = min(candidate_score for _, candidate_score in candidates)
    worst_id = max(
        item_id for item_id, item_score in candidates if item_score == lowest_score
    )
    worst_index = candidates.index((worst_id, lowest_score))
    worst_id, worst_score = candidates[worst_index]
    if score > worst_score or (score == worst_score and nct_id < worst_id):
        candidates[worst_index] = (nct_id, score)


def _metric_value(result: dict[str, object], key: str) -> float:
    value = result[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TrecEvaluationError(f"TREC metric {key} was not numeric.")
    return float(value)
