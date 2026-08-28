"""Evaluation runners which share the production lexical scorer and criterion engine."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

from src.criteria.eligibility_parser import (
    ParsedCriterion,
    parse_eligibility_text_with_review_metadata,
)
from src.criteria.evaluation import evaluate_atomic_criterion
from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.criteria.schemas import AtomicCriterion
from src.db.models import Trial
from src.evaluation.metrics import (
    excluded_rate_at_k,
    label_recall,
    macro_f1,
    mean,
    ndcg_at_k,
    outcome_counts,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from src.fhir.schemas import PatientFact
from src.retrieval.fusion import (
    RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
    RECIPROCAL_RANK_FUSION_VERSION,
    fuse_ranked_trial_candidates,
)
from src.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate
from src.retrieval.semantic import SemanticTrialCandidate
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


class EvaluationDatasetError(ValueError):
    """Raised when an evaluation input is incomplete or changes unexpectedly."""


_RETRIEVAL_ACCEPTANCE_METRICS = frozenset(
    {
        "nDCG@5",
        "nDCG@10",
        "Precision@5",
        "Precision@10",
        "Recall@50",
        "MRR",
    }
)
_TREC_SOURCE_HASHES = {
    "2021": {
        "topics_sha256": (
            "94bda921ce7c40a0353f251abb2ea938c77331759a9f83a36abd145ab5840aca"
        ),
        "qrels_sha256": (
            "ba7a2cddc90285e75cd76adcd483394a6c9bacf7017113222058ba6537e6d8ac"
        ),
    },
    "2022": {
        "topics_sha256": (
            "c5d37709ba14f6cb341b0bea35a7f43bd1cf93647f939659667975229a7abe91"
        ),
        "qrels_sha256": (
            "e569a531489e03f7b1fab03fe169c8ea66f4a59e8180fa9858b1a6e4bdcb0c5c"
        ),
    },
}


def read_json(path: Path) -> dict[str, Any]:
    """Read one committed evaluation fixture without accepting arbitrary shapes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationDatasetError(
            f"Could not read evaluation dataset: {path}"
        ) from error
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("Evaluation datasets must contain a JSON object.")
    return payload


def evaluate_retrieval_dataset(path: Path) -> dict[str, Any]:
    """Run the production lexical ranking function against a frozen trial corpus."""
    payload = read_json(path)
    trials_payload = _required_list(payload, "trials")
    topics = _required_list(payload, "topics")
    trials = tuple(_trial_from_payload(item) for item in trials_payload)
    topic_results: list[dict[str, Any]] = []

    for topic in topics:
        if not isinstance(topic, dict):
            raise EvaluationDatasetError("Each retrieval topic must be an object.")
        topic_id = _required_text(topic, "id")
        terms = _required_list(topic, "terms")
        judgments = topic.get("judgments")
        if not isinstance(judgments, dict):
            raise EvaluationDatasetError(
                f"Topic {topic_id} requires a judgments object."
            )
        query = PatientDerivedRetrievalQuery(
            terms=[_retrieval_term(term) for term in terms]
        )
        started_at = time.perf_counter()
        ranked = _rank_lexical_trials(trials, query)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        ranked_ids = [trial.nct_id for trial, _ in ranked]
        relevances = [_judgment(judgments, nct_id) for nct_id in ranked_ids]
        ideal_relevances = [_judgment(judgments, nct_id) for nct_id in judgments]
        eligible_count = sum(value == 2 for value in ideal_relevances)
        topic_results.append(
            {
                "topic_id": topic_id,
                "ranked_nct_ids": ranked_ids,
                "latency_ms": elapsed_ms,
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

    return {
        "evaluation": "retrieval",
        "dataset": str(path),
        "configuration": payload.get("configuration", {}),
        "topic_count": len(topic_results),
        "metrics": {
            key: mean(result[key] for result in topic_results)
            for key in (
                "nDCG@5",
                "nDCG@10",
                "Precision@5",
                "Precision@10",
                "Recall@50",
                "MRR",
                "excluded_trial_rate_top_10",
            )
        }
        | {"mean_latency_ms": mean(result["latency_ms"] for result in topic_results)},
        "topics": topic_results,
    }


def evaluate_parser_dataset(path: Path) -> dict[str, Any]:
    """Measure eligibility parsing without retrieval, facts, or match outcomes.

    Parsing quality is deliberately reported before the criterion evaluator and
    candidate retrieval layers. A parser error must remain visible rather than
    being obscured by later matching behavior.
    """
    payload = read_json(path)
    configuration = _parser_evaluation_configuration(payload)
    cases = _required_list(payload, "cases")
    exact_expected: set[str] = set()
    exact_predicted: set[str] = set()
    span_expected: set[str] = set()
    span_predicted: set[str] = set()
    review_expected: set[str] = set()
    review_predicted: set[str] = set()
    abstention_hits = 0
    results: list[dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationDatasetError("Each parser case must be an object.")
        case_id = _required_text(case, "id")
        eligibility_text = _required_text(case, "eligibility_text")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise EvaluationDatasetError(
                f"Parser case {case_id} requires expected data."
            )
        expected_criteria = _expected_parser_criteria(expected, case_id=case_id)
        expected_reviews = _expected_parser_review_signals(expected, case_id=case_id)
        expect_abstention = _required_bool(expected, "expect_abstention")
        predicted = parse_eligibility_text_with_review_metadata(eligibility_text)
        expected_exact = {
            _criterion_signature(criterion) for criterion in expected_criteria
        }
        predicted_exact = {
            _parsed_criterion_signature(criterion) for criterion in predicted
        }
        expected_spans = {
            _criterion_span_signature(criterion) for criterion in expected_criteria
        }
        predicted_spans = {
            _parsed_criterion_span_signature(criterion) for criterion in predicted
        }
        expected_review_keys = {
            _review_signal_signature(signal, reason)
            for signal in expected_reviews
            for reason in signal["reasons"]
        }
        predicted_review_keys = {
            _parsed_review_signal_signature(criterion, reason)
            for criterion in predicted
            for reason in criterion.review_reasons
        }
        exact_expected.update(f"{case_id}:{key}" for key in expected_exact)
        exact_predicted.update(f"{case_id}:{key}" for key in predicted_exact)
        span_expected.update(f"{case_id}:{key}" for key in expected_spans)
        span_predicted.update(f"{case_id}:{key}" for key in predicted_spans)
        review_expected.update(f"{case_id}:{key}" for key in expected_review_keys)
        review_predicted.update(f"{case_id}:{key}" for key in predicted_review_keys)
        predicted_abstention = not predicted
        abstention_hits += predicted_abstention == expect_abstention
        results.append(
            {
                "case_id": case_id,
                "expected_criterion_count": len(expected_criteria),
                "predicted_criterion_count": len(predicted),
                "expect_abstention": expect_abstention,
                "predicted_abstention": predicted_abstention,
                "expected_review_signals": sorted(expected_review_keys),
                "predicted_review_signals": sorted(predicted_review_keys),
            }
        )

    return {
        "evaluation": "eligibility-parser",
        "scope": (
            "eligibility text to atomic criteria only; excludes retrieval, patient "
            "facts, criterion evaluation, and final match aggregation"
        ),
        "dataset": str(path),
        "configuration": configuration,
        "case_count": len(results),
        "metrics": {
            "source_span": _set_precision_recall_f1(span_expected, span_predicted),
            "atomic_rule": _set_precision_recall_f1(exact_expected, exact_predicted),
            "review_signal": _set_precision_recall_f1(
                review_expected, review_predicted
            ),
            "abstention_accuracy": abstention_hits / len(results) if results else 0.0,
        },
        "cases": results,
    }


def compare_held_out_retrieval_dataset(path: Path) -> dict[str, Any]:
    """Compare lexical-only and production-RRF ranks on held-out inputs.

    The semantic ranking is supplied as a pinned, precomputed public-trial list
    for each topic. This keeps benchmark query text outside the application and
    makes the comparison repeatable without re-encoding an historical corpus.
    """
    payload = read_json(path)
    configuration = _held_out_comparison_configuration(payload)
    candidate_limit = _required_positive_int(payload, "candidate_limit")
    trials_payload = _required_list(payload, "trials")
    topics = _required_list(payload, "topics")
    trials = tuple(_trial_from_payload(item) for item in trials_payload)
    trials_by_nct = {trial.nct_id: trial for trial in trials}
    lexical_topic_results: list[dict[str, Any]] = []
    hybrid_topic_results: list[dict[str, Any]] = []

    for topic in topics:
        if not isinstance(topic, dict):
            raise EvaluationDatasetError("Each retrieval topic must be an object.")
        topic_id = _required_text(topic, "id")
        if topic.get("split") != "heldout":
            raise EvaluationDatasetError(
                f"Topic {topic_id} must be explicitly marked heldout."
            )
        terms = _required_list(topic, "terms")
        judgments = topic.get("judgments")
        if not isinstance(judgments, dict):
            raise EvaluationDatasetError(
                f"Topic {topic_id} requires a judgments object."
            )
        query = PatientDerivedRetrievalQuery(
            terms=[_retrieval_term(term) for term in terms]
        )
        started_at = time.perf_counter()
        ranked_lexical = _rank_lexical_trials(trials, query)[:candidate_limit]
        lexical_elapsed_ms = (time.perf_counter() - started_at) * 1000
        semantic_candidates = _precomputed_semantic_candidates(
            topic,
            topic_id=topic_id,
            trials_by_nct=trials_by_nct,
            candidate_limit=candidate_limit,
        )
        started_at = time.perf_counter()
        ranked_hybrid = fuse_ranked_trial_candidates(
            ranked_lexical,
            semantic_candidates,
            candidate_limit=candidate_limit,
        )
        hybrid_elapsed_ms = (time.perf_counter() - started_at) * 1000
        lexical_topic_results.append(
            _retrieval_topic_result(
                topic_id=topic_id,
                ranked_ids=[trial.nct_id for trial, _ in ranked_lexical],
                judgments=judgments,
                latency_ms=lexical_elapsed_ms,
            )
        )
        hybrid_topic_results.append(
            _retrieval_topic_result(
                topic_id=topic_id,
                ranked_ids=[trial.nct_id for trial, _ in ranked_hybrid],
                judgments=judgments,
                latency_ms=hybrid_elapsed_ms,
            )
        )

    lexical_metrics = _mean_retrieval_metrics(lexical_topic_results)
    hybrid_metrics = _mean_retrieval_metrics(hybrid_topic_results)
    acceptance = _held_out_acceptance(
        configuration, lexical_metrics=lexical_metrics, hybrid_metrics=hybrid_metrics
    )
    return {
        "evaluation": "held-out-trec-retrieval-comparison",
        "dataset": str(path),
        "configuration": configuration,
        "candidate_limit": candidate_limit,
        "topic_count": len(lexical_topic_results),
        "latency_scope": (
            "ranking only; precomputed semantic lists exclude model encoding time"
        ),
        "lexical_only": {"metrics": lexical_metrics, "topics": lexical_topic_results},
        "hybrid_rrf": {"metrics": hybrid_metrics, "topics": hybrid_topic_results},
        "delta_hybrid_minus_lexical": {
            key: hybrid_metrics[key] - lexical_metrics[key] for key in lexical_metrics
        },
        "acceptance": acceptance,
    }


def evaluate_criteria_dataset(path: Path) -> dict[str, Any]:
    """Evaluate carefully annotated synthetic atomic-criterion cases."""
    payload = read_json(path)
    as_of = _as_of_date(payload)
    cases = _required_list(payload, "cases")
    results: list[dict[str, Any]] = []
    expected_outcomes: list[str] = []
    predicted_outcomes: list[str] = []
    predicted_evidence_ids: list[str] = []
    evidence_hits = 0
    exclusion_expected: list[str] = []
    exclusion_predicted: list[str] = []
    expected_review_cases = 0
    false_clearances = 0

    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationDatasetError("Each criterion case must be an object.")
        case_id = _required_text(case, "id")
        criterion_payload = case.get("criterion")
        if not isinstance(criterion_payload, dict):
            raise EvaluationDatasetError(f"Case {case_id} requires a criterion object.")
        facts_payload = _required_list(case, "facts")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise EvaluationDatasetError(f"Case {case_id} requires an expected object.")
        expected_outcome = _required_text(expected, "outcome")
        expected_ids = _required_list(expected, "evidence_fact_ids")
        criterion = AtomicCriterion.model_validate(criterion_payload)
        facts = [PatientFact.model_validate(fact) for fact in facts_payload]
        result = evaluate_atomic_criterion(criterion, facts, as_of=as_of)

        expected_outcomes.append(expected_outcome)
        predicted_outcomes.append(result.outcome)
        predicted_evidence_ids.extend(result.evidence_fact_ids)
        expected_evidence_set = {str(value) for value in expected_ids}
        evidence_hits += sum(
            evidence_id in expected_evidence_set
            for evidence_id in result.evidence_fact_ids
        )
        if criterion.category == "exclusion":
            exclusion_expected.append(expected_outcome)
            exclusion_predicted.append(result.outcome)
        if expected_outcome in {"unknown", "conflicting"}:
            expected_review_cases += 1
            if (
                result.outcome not in {"unknown", "conflicting"}
                and not result.requires_review
            ):
                false_clearances += 1
        results.append(
            {
                "case_id": case_id,
                "expected_outcome": expected_outcome,
                "predicted_outcome": result.outcome,
                "expected_evidence_fact_ids": expected_ids,
                "predicted_evidence_fact_ids": result.evidence_fact_ids,
                "requires_review": result.requires_review,
            }
        )

    return {
        "evaluation": "criteria",
        "dataset": str(path),
        "configuration": payload.get("configuration", {}),
        "case_count": len(results),
        "metrics": {
            "criterion_macro_f1": macro_f1(expected_outcomes, predicted_outcomes),
            "exclusion_recall": label_recall(
                exclusion_expected, exclusion_predicted, label="not_met"
            ),
            "false_clearance_rate": (
                false_clearances / expected_review_cases
                if expected_review_cases
                else 0.0
            ),
            "evidence_precision": (
                evidence_hits / len(predicted_evidence_ids)
                if predicted_evidence_ids
                else 0.0
            ),
            "abstention_rate": mean(result["requires_review"] for result in results),
        },
        "predicted_outcomes": outcome_counts(predicted_outcomes),
        "cases": results,
    }


def verify_frozen_dataset(manifest_path: Path) -> dict[str, Any]:
    """Verify the bytes and deterministic versions of committed demo fixtures."""
    manifest = read_json(manifest_path)
    base_dir = manifest_path.parent
    files = _required_list(manifest, "files")
    verified: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            raise EvaluationDatasetError("Each frozen-manifest file must be an object.")
        relative_path = _required_text(item, "path")
        expected_hash = _required_text(item, "sha256")
        file_path = base_dir / relative_path
        actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise EvaluationDatasetError(
                f"Frozen dataset checksum mismatch for {relative_path}."
            )
        verified.append(relative_path)
    return {
        "evaluation": "frozen-dataset-verification",
        "manifest": str(manifest_path),
        "configuration": manifest.get("configuration", {}),
        "verified_files": verified,
    }


def _rank_lexical_trials(
    trials: Iterable[Trial], query: PatientDerivedRetrievalQuery
) -> list[tuple[Trial, dict[str, Any]]]:
    scored = (
        (trial, score)
        for trial in trials
        if (score := score_trial_candidate(trial, query)) is not None
    )
    return rank_scored_trials(scored)


def _parser_evaluation_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise EvaluationDatasetError("Parser evaluation requires configuration.")
    if configuration.get("parser") != ELIGIBILITY_PARSER_CONFIGURATION.snapshot():
        raise EvaluationDatasetError(
            "Parser evaluation must use the pinned parser configuration."
        )
    return configuration


def _expected_parser_criteria(
    expected: dict[str, Any], *, case_id: str
) -> list[AtomicCriterion]:
    criteria_payload = _required_list(expected, "criteria")
    try:
        return [AtomicCriterion.model_validate(item) for item in criteria_payload]
    except (TypeError, ValueError) as error:
        raise EvaluationDatasetError(
            f"Parser case {case_id} has an invalid expected criterion."
        ) from error


def _expected_parser_review_signals(
    expected: dict[str, Any], *, case_id: str
) -> list[dict[str, Any]]:
    signals = _required_list(expected, "review_signals")
    validated: list[dict[str, Any]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            raise EvaluationDatasetError(
                f"Parser case {case_id} review signals must be objects."
            )
        start = signal.get("source_start")
        end = signal.get("source_end")
        reasons = signal.get("reasons")
        if type(start) is not int or type(end) is not int or end <= start:
            raise EvaluationDatasetError(
                f"Parser case {case_id} review signal requires a valid source span."
            )
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(
                reason in {"ambiguous_clause", "nested_clause", "low_confidence_parse"}
                for reason in reasons
            )
        ):
            raise EvaluationDatasetError(
                f"Parser case {case_id} has invalid review-signal reasons."
            )
        validated.append({"source_start": start, "source_end": end, "reasons": reasons})
    return validated


def _criterion_signature(criterion: AtomicCriterion) -> str:
    return json.dumps(
        {
            "category": criterion.category,
            "source_start": criterion.source_start,
            "source_end": criterion.source_end,
            "rule": criterion.rule.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _parsed_criterion_signature(criterion: ParsedCriterion) -> str:
    return _criterion_signature(criterion.criterion)


def _criterion_span_signature(criterion: AtomicCriterion) -> str:
    return f"{criterion.category}:{criterion.source_start}:{criterion.source_end}"


def _parsed_criterion_span_signature(criterion: ParsedCriterion) -> str:
    return _criterion_span_signature(criterion.criterion)


def _review_signal_signature(signal: dict[str, Any], reason: str) -> str:
    return ":".join([str(signal["source_start"]), str(signal["source_end"]), reason])


def _parsed_review_signal_signature(criterion: ParsedCriterion, reason: str) -> str:
    return ":".join(
        [
            str(criterion.criterion.source_start),
            str(criterion.criterion.source_end),
            reason,
        ]
    )


def _set_precision_recall_f1(
    expected: set[str], predicted: set[str]
) -> dict[str, float]:
    true_positives = len(expected & predicted)
    precision = true_positives / len(predicted) if predicted else float(not expected)
    recall = true_positives / len(expected) if expected else float(not predicted)
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
    }


def _held_out_comparison_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise EvaluationDatasetError("Held-out comparison requires configuration.")
    if configuration.get("term_mapping_version") in {None, ""}:
        raise EvaluationDatasetError(
            "Held-out comparison requires a reviewed term_mapping_version."
        )
    if configuration.get("semantic_model") != SEMANTIC_EMBEDDING_MODEL.snapshot():
        raise EvaluationDatasetError(
            "Held-out comparison must use the pinned semantic model configuration."
        )
    if configuration.get("rank_fusion") != {
        "method": RECIPROCAL_RANK_FUSION_VERSION,
        "rank_constant": RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
    }:
        raise EvaluationDatasetError(
            "Held-out comparison must use the production reciprocal-rank fusion "
            "configuration."
        )
    benchmark = configuration.get("benchmark")
    if not isinstance(benchmark, dict):
        raise EvaluationDatasetError("Held-out comparison requires benchmark metadata.")
    benchmark_kind = benchmark.get("kind")
    if benchmark_kind not in {"synthetic_regression", "trec_clinical_trials"}:
        raise EvaluationDatasetError(
            "Held-out benchmark kind must be synthetic_regression or "
            "trec_clinical_trials."
        )
    if benchmark_kind == "trec_clinical_trials":
        year = benchmark.get("year")
        if year not in _TREC_SOURCE_HASHES:
            raise EvaluationDatasetError(
                "TREC benchmark requires supported source year."
            )
        expected_hashes = _TREC_SOURCE_HASHES[cast(str, year)]
        if any(
            benchmark.get(field) != expected_value
            for field, expected_value in expected_hashes.items()
        ):
            raise EvaluationDatasetError(
                "TREC benchmark must retain the official topic and qrels checksums."
            )
        if benchmark.get("corpus_manifest_sha256") in {None, ""}:
            raise EvaluationDatasetError(
                "TREC benchmark requires a reviewed historical corpus manifest hash."
            )
    acceptance = configuration.get("acceptance_gate")
    if not isinstance(acceptance, dict):
        raise EvaluationDatasetError("Held-out comparison requires an acceptance_gate.")
    agreed_metric = acceptance.get("agreed_metric")
    if agreed_metric not in _RETRIEVAL_ACCEPTANCE_METRICS:
        raise EvaluationDatasetError(
            "Held-out acceptance_gate requires a supported agreed_metric."
        )
    minimum_improvement = acceptance.get("minimum_improvement")
    if (
        not isinstance(minimum_improvement, (int, float))
        or isinstance(minimum_improvement, bool)
        or minimum_improvement <= 0
    ):
        raise EvaluationDatasetError(
            "Held-out acceptance_gate minimum_improvement must be positive."
        )
    maximum_excluded_increase = acceptance.get(
        "maximum_excluded_trial_rate_top_10_increase"
    )
    if (
        not isinstance(maximum_excluded_increase, (int, float))
        or isinstance(maximum_excluded_increase, bool)
        or maximum_excluded_increase < 0
    ):
        raise EvaluationDatasetError(
            "Held-out acceptance_gate excluded-trial increase must be non-negative."
        )
    return configuration


def _held_out_acceptance(
    configuration: dict[str, Any],
    *,
    lexical_metrics: dict[str, float],
    hybrid_metrics: dict[str, float],
) -> dict[str, float | str | bool]:
    """Report the explicit safety gate without promoting synthetic regression data."""
    acceptance = cast(dict[str, Any], configuration["acceptance_gate"])
    agreed_metric = cast(str, acceptance["agreed_metric"])
    minimum_improvement = float(acceptance["minimum_improvement"])
    maximum_excluded_increase = float(
        acceptance["maximum_excluded_trial_rate_top_10_increase"]
    )
    observed_improvement = (
        hybrid_metrics[agreed_metric] - lexical_metrics[agreed_metric]
    )
    excluded_rate_increase = (
        hybrid_metrics["excluded_trial_rate_top_10"]
        - lexical_metrics["excluded_trial_rate_top_10"]
    )
    is_claimable = configuration["benchmark"]["kind"] == "trec_clinical_trials"
    return {
        "benchmark_kind": cast(str, configuration["benchmark"]["kind"]),
        "claimable": is_claimable,
        "agreed_metric": agreed_metric,
        "minimum_improvement": minimum_improvement,
        "observed_improvement": observed_improvement,
        "maximum_excluded_trial_rate_top_10_increase": maximum_excluded_increase,
        "observed_excluded_trial_rate_top_10_increase": excluded_rate_increase,
        "passed": (
            is_claimable
            and observed_improvement >= minimum_improvement
            and excluded_rate_increase <= maximum_excluded_increase
        ),
    }


def _precomputed_semantic_candidates(
    topic: dict[str, Any],
    *,
    topic_id: str,
    trials_by_nct: dict[str, Trial],
    candidate_limit: int,
) -> tuple[SemanticTrialCandidate, ...]:
    source_ids = _required_list(topic, "semantic_ranked_nct_ids")[:candidate_limit]
    candidates: list[SemanticTrialCandidate] = []
    seen_ids: set[str] = set()
    for rank, nct_id in enumerate(source_ids, 1):
        if not isinstance(nct_id, str) or not nct_id.strip():
            raise EvaluationDatasetError(
                f"Topic {topic_id} semantic_ranked_nct_ids must contain NCT IDs."
            )
        if nct_id in seen_ids:
            raise EvaluationDatasetError(
                f"Topic {topic_id} semantic_ranked_nct_ids must be unique."
            )
        trial = trials_by_nct.get(nct_id)
        if trial is None:
            raise EvaluationDatasetError(
                f"Topic {topic_id} semantic ranking references an absent trial."
            )
        seen_ids.add(nct_id)
        candidates.append(SemanticTrialCandidate(trial=trial, score=0.0, rank=rank))
    return tuple(candidates)


def _retrieval_topic_result(
    *,
    topic_id: str,
    ranked_ids: list[str],
    judgments: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    relevances = [_judgment(judgments, nct_id) for nct_id in ranked_ids]
    ideal_relevances = [_judgment(judgments, nct_id) for nct_id in judgments]
    eligible_count = sum(value == 2 for value in ideal_relevances)
    return {
        "topic_id": topic_id,
        "ranked_nct_ids": ranked_ids,
        "latency_ms": latency_ms,
        "nDCG@5": ndcg_at_k(relevances, ideal_relevances=ideal_relevances, k=5),
        "nDCG@10": ndcg_at_k(relevances, ideal_relevances=ideal_relevances, k=10),
        "Precision@5": precision_at_k(relevances, k=5),
        "Precision@10": precision_at_k(relevances, k=10),
        "Recall@50": recall_at_k(relevances, total_relevant=eligible_count, k=50),
        "MRR": reciprocal_rank(relevances),
        "excluded_trial_rate_top_10": excluded_rate_at_k(relevances, k=10),
    }


def _mean_retrieval_metrics(topic_results: list[dict[str, Any]]) -> dict[str, float]:
    metrics = {
        key: mean(float(result[key]) for result in topic_results)
        for key in (
            "nDCG@5",
            "nDCG@10",
            "Precision@5",
            "Precision@10",
            "Recall@50",
            "MRR",
            "excluded_trial_rate_top_10",
        )
    }
    metrics["mean_latency_ms"] = mean(
        float(result["latency_ms"]) for result in topic_results
    )
    return metrics


def _trial_from_payload(payload: object) -> Trial:
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("Each trial must be an object.")
    nct_id = _required_text(payload, "nct_id")
    return Trial(
        nct_id=nct_id,
        title=_optional_text(payload.get("title")),
        conditions=_string_list(payload.get("conditions", []), field="conditions"),
        interventions=_object_list(
            payload.get("interventions", []), field="interventions"
        ),
        eligibility_text=_optional_text(payload.get("eligibility_text")),
    )


def _retrieval_term(payload: object) -> RetrievalTerm:
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("Each retrieval term must be an object.")
    kind = _required_text(payload, "kind")
    if kind not in {"condition", "medication", "procedure"}:
        raise EvaluationDatasetError(
            "Retrieval term kind must be condition, medication, or procedure."
        )
    return RetrievalTerm(
        text=_required_text(payload, "text"),
        source_fact_id=_required_text(payload, "source_fact_id"),
        kind=cast(Literal["condition", "medication", "procedure"], kind),
    )


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise EvaluationDatasetError(f"Evaluation dataset requires a {key} list.")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 1:
        raise EvaluationDatasetError(
            f"Evaluation dataset requires a positive integer {key}."
        )
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if type(value) is not bool:
        raise EvaluationDatasetError(f"Evaluation dataset requires boolean {key}.")
    return value


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDatasetError(
            f"Evaluation dataset requires non-empty {key} text."
        )
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationDatasetError(f"Trial {field} must be a list of strings.")
    return value


def _object_list(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EvaluationDatasetError(f"Trial {field} must be a list of objects.")
    return value


def _judgment(judgments: dict[str, Any], nct_id: str) -> int:
    value = judgments.get(nct_id, 0)
    if not isinstance(value, int) or value not in {0, 1, 2}:
        raise EvaluationDatasetError(
            "Retrieval judgments must be integer TREC grades 0, 1, or 2."
        )
    return value


def _as_of_date(payload: dict[str, Any]) -> date:
    value = payload.get("as_of")
    if not isinstance(value, str):
        raise EvaluationDatasetError("Criterion dataset requires an ISO as_of date.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise EvaluationDatasetError("Criterion as_of must be an ISO date.") from error
