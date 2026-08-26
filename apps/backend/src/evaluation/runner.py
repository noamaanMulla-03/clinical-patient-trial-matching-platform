"""Evaluation runners which share the production lexical scorer and criterion engine."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

from src.criteria.evaluation import evaluate_atomic_criterion
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
from src.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate


class EvaluationDatasetError(ValueError):
    """Raised when an evaluation input is incomplete or changes unexpectedly."""


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
