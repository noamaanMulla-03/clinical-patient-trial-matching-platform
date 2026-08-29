"""Synthetic-FHIR regression evaluation using production normalization and rules.

This is an engineering benchmark, not clinical validation. Its committed labels
must remain explicitly marked as not clinician-reviewed until that review occurs.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.criteria.evaluation import evaluate_atomic_criterion
from src.criteria.schemas import AtomicCriterion
from src.db.models import Trial
from src.evaluation.metrics import label_recall, macro_f1, mean
from src.evaluation.runner import EvaluationDatasetError, read_json
from src.fhir.importer import normalize_patient_resource
from src.fhir.safety import require_synthetic_fhir_bundle
from src.retrieval.query_builder import build_patient_retrieval_query
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate


def evaluate_synthetic_fhir_benchmark(path: Path) -> dict[str, Any]:
    """Measure the synthetic FHIR-to-candidate-and-rule path without persistence.

    Patient input remains in memory, and the report retains only fixture IDs,
    candidate NCT IDs, and controlled outcome codes. This avoids turning the
    benchmark command into another patient-data retention path.
    """
    payload = read_json(path)
    configuration = _configuration(payload)
    as_of = _as_of(payload)
    review_limit = _positive_int(payload, "review_limit")
    cases = _list(payload, "cases")
    candidate_recalls: list[float] = []
    expected_outcomes: list[str] = []
    predicted_outcomes: list[str] = []
    expected_exclusions: list[str] = []
    predicted_exclusions: list[str] = []
    expected_review = 0
    false_clearances = 0
    evidence_expected = 0
    evidence_hits = 0
    results: list[dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationDatasetError(
                "Synthetic FHIR benchmark cases must be objects."
            )
        case_id = _text(case, "id")
        bundle = case.get("bundle")
        if not isinstance(bundle, dict):
            raise EvaluationDatasetError(f"Case {case_id} requires a FHIR Bundle.")
        require_synthetic_fhir_bundle(bundle)
        normalized = normalize_patient_resource(
            bundle,
            patient_import_id=uuid5(
                NAMESPACE_URL, f"synthetic-fhir-benchmark:{case_id}"
            ),
            evaluated_at=datetime.combine(as_of, datetime.min.time()),
        )
        query = build_patient_retrieval_query(normalized.facts, as_of=as_of)
        trials = _trials(case, case_id=case_id)
        ranked = rank_scored_trials(
            (trial, score)
            for trial in trials
            if (score := score_trial_candidate(trial, query)) is not None
        )[:review_limit]
        ranked_ids = [trial.nct_id for trial, _ in ranked]
        expected_candidates = set(_string_list(case.get("expected_candidates")))
        candidate_recalls.append(
            len(expected_candidates & set(ranked_ids)) / len(expected_candidates)
            if expected_candidates
            else 1.0
        )

        trial_results: list[dict[str, Any]] = []
        for trial_payload in _list(case, "trials"):
            if not isinstance(trial_payload, dict):
                raise EvaluationDatasetError(f"Case {case_id} trial must be an object.")
            nct_id = _text(trial_payload, "nct_id")
            expected = trial_payload.get("expected")
            if not isinstance(expected, dict):
                raise EvaluationDatasetError(
                    f"Case {case_id} trial {nct_id} requires expected."
                )
            expected_outcome = _text(expected, "outcome")
            if nct_id not in ranked_ids:
                if expected_outcome != "not_retrieved":
                    raise EvaluationDatasetError(
                        f"Case {case_id} expected trial {nct_id} was not retrieved."
                    )
                continue
            criterion_results = []
            for item in _list(trial_payload, "criteria"):
                if not isinstance(item, dict):
                    raise EvaluationDatasetError("Benchmark criteria must be objects.")
                criterion = AtomicCriterion.model_validate(item.get("criterion"))
                evaluation = evaluate_atomic_criterion(
                    criterion, normalized.facts, as_of=as_of
                )
                expected_criterion = item.get("expected_outcome")
                if not isinstance(expected_criterion, str):
                    raise EvaluationDatasetError(
                        "Benchmark criterion requires expected_outcome."
                    )
                if evaluation.outcome != expected_criterion:
                    raise EvaluationDatasetError(
                        f"Case {case_id} criterion outcome changed for {nct_id}."
                    )
                criterion_results.append((criterion.category, evaluation))
                evidence_expected += 1
                evidence_hits += bool(evaluation.evidence_fact_ids)

            outcome = _aggregate(criterion_results)
            if expected_outcome not in {
                "potential_match",
                "likely_excluded",
                "needs_review",
                "not_relevant",
            }:
                raise EvaluationDatasetError(
                    f"Case {case_id} has an invalid expected outcome."
                )
            expected_outcomes.append(expected_outcome)
            predicted_outcomes.append(outcome)
            if expected_outcome == "likely_excluded":
                expected_exclusions.append(expected_outcome)
                predicted_exclusions.append(outcome)
            if expected_outcome == "needs_review":
                expected_review += 1
                false_clearances += outcome == "potential_match"
            trial_results.append(
                {
                    "nct_id": nct_id,
                    "outcome": outcome,
                    "criterion_outcomes": [
                        evaluation.outcome for _, evaluation in criterion_results
                    ],
                }
            )
        results.append(
            {
                "case_id": case_id,
                "normalized_fact_count": len(normalized.facts),
                "candidate_nct_ids": ranked_ids,
                "trial_results": trial_results,
            }
        )

    return {
        "evaluation": "synthetic-fhir-regression",
        "claimable": False,
        "scope": (
            "synthetic FHIR normalization, lexical candidate retrieval, and "
            "deterministic criteria; not clinician validation"
        ),
        "configuration": configuration,
        "case_count": len(results),
        "metrics": {
            "expected_candidate_recall_at_review_limit": mean(candidate_recalls),
            "outcome_macro_f1": macro_f1(expected_outcomes, predicted_outcomes),
            "exclusion_recall": label_recall(
                expected_exclusions, predicted_exclusions, label="likely_excluded"
            ),
            "false_clearance_rate": false_clearances / expected_review
            if expected_review
            else 0.0,
            "evidence_presence_rate": evidence_hits / evidence_expected
            if evidence_expected
            else 0.0,
            "needs_review_rate": mean(
                outcome == "needs_review" for outcome in predicted_outcomes
            ),
        },
        "cases": results,
    }


def _aggregate(results: list[tuple[str, Any]]) -> str:
    if not results or any(
        evaluation.requires_review or evaluation.outcome in {"unknown", "conflicting"}
        for _, evaluation in results
    ):
        return "needs_review"
    if any(
        category == "exclusion" and evaluation.outcome == "not_met"
        for category, evaluation in results
    ):
        return "likely_excluded"
    if any(
        category == "inclusion" and evaluation.outcome == "not_met"
        for category, evaluation in results
    ):
        return "not_relevant"
    return "potential_match"


def _configuration(payload: dict[str, Any]) -> dict[str, Any]:
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise EvaluationDatasetError("Synthetic FHIR benchmark requires configuration.")
    if (
        configuration.get("annotation_status")
        != "engineering_annotated_not_clinician_reviewed"
    ):
        raise EvaluationDatasetError(
            "Synthetic FHIR benchmark must declare annotation status."
        )
    return configuration


def _as_of(payload: dict[str, Any]) -> date:
    try:
        return date.fromisoformat(_text(payload, "as_of"))
    except ValueError as error:
        raise EvaluationDatasetError(
            "Synthetic FHIR benchmark requires ISO as_of date."
        ) from error


def _trials(case: dict[str, Any], *, case_id: str) -> list[Trial]:
    trials: list[Trial] = []
    for payload in _list(case, "trials"):
        if not isinstance(payload, dict):
            raise EvaluationDatasetError(f"Case {case_id} trial must be an object.")
        trials.append(
            Trial(
                nct_id=_text(payload, "nct_id"),
                title=_text(payload, "title"),
                conditions=_string_list(payload.get("conditions")),
                interventions=payload.get("interventions", []),
                eligibility_text=_text(payload, "eligibility_text"),
            )
        )
    return trials


def _list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise EvaluationDatasetError(f"Synthetic FHIR benchmark requires {key} list.")
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationDatasetError("Synthetic FHIR benchmark requires text lists.")
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDatasetError(f"Synthetic FHIR benchmark requires {key} text.")
    return value


def _positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 1:
        raise EvaluationDatasetError(
            f"Synthetic FHIR benchmark requires positive {key}."
        )
    return value
