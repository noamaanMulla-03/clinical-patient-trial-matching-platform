"""Exit-check coverage for deterministic criterion safety guarantees."""

from datetime import date
from uuid import uuid4

from src.criteria.aggregation import _outcome_for
from src.criteria.evaluation import evaluate_atomic_criterion
from src.criteria.schemas import AtomicCriterion
from src.db.models import Criterion, CriterionResult
from src.fhir.schemas import (
    ClinicalCode,
    FHIRProvenance,
    ObservationFactValue,
    PatientFact,
)


def _atomic(rule: dict[str, object], *, category: str = "inclusion") -> AtomicCriterion:
    return AtomicCriterion(
        category=category,
        source_text="criterion",
        source_start=0,
        source_end=9,
        rule=rule,
    )


def _result(
    criterion: Criterion, *, outcome: str, requires_review: bool
) -> CriterionResult:
    return CriterionResult(
        id=uuid4(),
        criterion_id=criterion.id,
        outcome=outcome,
        requires_review=requires_review,
    )


def test_missing_facts_never_produce_a_resolved_criterion_outcome() -> None:
    rules: list[dict[str, object]] = [
        {"kind": "age", "operator": "at_least", "years": 18},
        {"kind": "recorded_sex", "value": "female"},
        {
            "kind": "coded_condition",
            "code": {"system": "http://snomed.info/sct", "value": "44054006"},
        },
        {
            "kind": "numeric_lab_threshold",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "comparator": ">=",
            "threshold": 7.0,
            "unit": "mmol/L",
        },
        {
            "kind": "date_window",
            "fact_kind": "observation",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "starts_on": "2026-01-01",
        },
        {
            "kind": "recency_window",
            "fact_kind": "observation",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "within_days": 30,
        },
        {
            "kind": "medication_status",
            "code": {"system": "http://rxnorm.info", "value": "123"},
            "expected_status": "active",
        },
        {
            "kind": "procedure_status",
            "code": {"system": "http://snomed.info/sct", "value": "456"},
            "expected_status": "completed",
        },
    ]

    results = [
        evaluate_atomic_criterion(_atomic(rule), [], as_of=date(2026, 8, 23))
        for rule in rules
    ]

    assert all(result.outcome == "unknown" for result in results)
    assert all(result.requires_review for result in results)


def test_supported_exclusion_aggregates_to_likely_excluded() -> None:
    criterion = _atomic(
        {
            "kind": "coded_condition",
            "code": {"system": "http://snomed.info/sct", "value": "44054006"},
        },
        category="exclusion",
    )
    fact = PatientFact(
        fact_id="documented-condition",
        patient_id="synthetic-patient",
        kind="condition",
        code=ClinicalCode(system="http://snomed.info/sct", value="44054006"),
        value=None,
        source=FHIRProvenance(resource_type="Condition", resource_id="condition"),
        source_resource={"resourceType": "Condition", "id": "condition"},
    )
    evaluation = evaluate_atomic_criterion(criterion, [fact], as_of=date(2026, 8, 23))
    stored_criterion = Criterion(id=uuid4(), category="exclusion")

    assert evaluation.outcome == "not_met"
    assert (
        _outcome_for(
            [stored_criterion],
            [
                _result(
                    stored_criterion,
                    outcome=evaluation.outcome,
                    requires_review=evaluation.requires_review,
                )
            ],
        )
        == "likely_excluded"
    )


def test_stale_or_conflicting_evidence_cannot_aggregate_to_potential_match() -> None:
    criterion = _atomic(
        {
            "kind": "numeric_lab_threshold",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "comparator": ">=",
            "threshold": 7.0,
            "unit": "mmol/L",
        }
    )
    stale_fact = PatientFact(
        fact_id="stale-glucose",
        patient_id="synthetic-patient",
        kind="observation",
        code=ClinicalCode(system="http://loinc.org", value="2345-7"),
        value=ObservationFactValue(numeric_value=9.0),
        unit="mmol/L",
        source=FHIRProvenance(resource_type="Observation", resource_id="glucose"),
        source_resource={"resourceType": "Observation", "id": "glucose"},
        quality_issues=[
            {"code": "stale", "field": "effectiveDateTime", "message": "Stale."}
        ],
    )
    stale_evaluation = evaluate_atomic_criterion(
        criterion, [stale_fact], as_of=date(2026, 8, 23)
    )
    stored_criterion = Criterion(id=uuid4(), category="inclusion")

    assert stale_evaluation.outcome == "unknown"
    assert (
        _outcome_for(
            [stored_criterion],
            [
                _result(
                    stored_criterion,
                    outcome=stale_evaluation.outcome,
                    requires_review=stale_evaluation.requires_review,
                )
            ],
        )
        == "needs_review"
    )
    assert (
        _outcome_for(
            [stored_criterion],
            [_result(stored_criterion, outcome="conflicting", requires_review=True)],
        )
        == "needs_review"
    )
