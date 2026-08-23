"""Deterministic evaluation checks for manually authored atomic criteria."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.criteria.evaluation import evaluate_atomic_criterion
from app.criteria.schemas import AtomicCriterion
from app.fhir.schemas import (
    ClinicalCode,
    FHIRProvenance,
    ObservationFactValue,
    PatientFact,
)


def _criterion(
    rule: dict[str, object], *, category: str = "inclusion"
) -> AtomicCriterion:
    source_text = "criterion"
    return AtomicCriterion(
        category=category,
        source_text=source_text,
        source_start=0,
        source_end=len(source_text),
        rule=rule,
    )


def _fact(
    *,
    fact_id: str,
    kind: str,
    code: ClinicalCode,
    value: object,
    unit: str | None = None,
    quality_issues: list[dict[str, str]] | None = None,
) -> PatientFact:
    resource_type = {
        "demographic": "Patient",
        "condition": "Condition",
        "observation": "Observation",
    }[kind]
    return PatientFact(
        fact_id=fact_id,
        patient_id="synthetic-patient",
        kind=kind,
        code=code,
        value=value,
        unit=unit,
        source=FHIRProvenance(resource_type=resource_type, resource_id=fact_id),
        source_resource={"resourceType": resource_type, "id": fact_id},
        quality_issues=quality_issues or [],
    )


def test_atomic_criterion_requires_source_text_to_match_its_exact_span() -> None:
    with pytest.raises(ValidationError, match="source span"):
        AtomicCriterion(
            category="inclusion",
            source_text="age 18",
            source_start=0,
            source_end=5,
            rule={"kind": "age", "operator": "at_least", "years": 18},
        )


def test_age_evaluation_uses_a_supplied_date_and_preserves_partial_date_ambiguity() -> (
    None
):
    criterion = _criterion({"kind": "age", "operator": "at_least", "years": 18})
    exact_birth_date = _fact(
        fact_id="birth-exact",
        kind="demographic",
        code=ClinicalCode(system="patient", value="birth-date"),
        value="2000-08-23",
    )
    partial_birth_date = _fact(
        fact_id="birth-partial",
        kind="demographic",
        code=ClinicalCode(system="patient", value="birth-date"),
        value="2008",
    )

    exact_result = evaluate_atomic_criterion(
        criterion, [exact_birth_date], as_of=date(2026, 8, 23)
    )
    ambiguous_result = evaluate_atomic_criterion(
        criterion, [partial_birth_date], as_of=date(2026, 8, 23)
    )

    assert exact_result.outcome == "met"
    assert exact_result.evidence_fact_ids == ["birth-exact"]
    assert ambiguous_result.outcome == "unknown"
    assert ambiguous_result.reason == "ambiguous_age"
    assert ambiguous_result.requires_review is True


def test_recorded_sex_evaluation_flips_a_proven_exclusion_predicate() -> None:
    criterion = _criterion(
        {"kind": "recorded_sex", "value": "female"}, category="exclusion"
    )
    fact = _fact(
        fact_id="gender",
        kind="demographic",
        code=ClinicalCode(system="patient", value="administrative-gender"),
        value="female",
    )

    result = evaluate_atomic_criterion(criterion, [fact], as_of=date(2026, 8, 23))

    assert result.outcome == "not_met"
    assert result.reason == "predicate_matched"


def test_coded_condition_evaluation_requires_an_exact_recorded_code() -> None:
    criterion = _criterion(
        {
            "kind": "coded_condition",
            "code": {"system": "http://snomed.info/sct", "value": "44054006"},
        }
    )
    condition = _fact(
        fact_id="condition",
        kind="condition",
        code=ClinicalCode(system="http://snomed.info/sct", value="44054006"),
        value=None,
    )
    different_system = _fact(
        fact_id="condition-other-system",
        kind="condition",
        code=ClinicalCode(system="http://example.test", value="44054006"),
        value=None,
    )

    matched = evaluate_atomic_criterion(criterion, [condition], as_of=date(2026, 8, 23))
    absent = evaluate_atomic_criterion(
        criterion, [different_system], as_of=date(2026, 8, 23)
    )

    assert matched.outcome == "met"
    assert matched.evidence_fact_ids == ["condition"]
    assert absent.outcome == "unknown"
    assert absent.reason == "missing_evidence"


def test_numeric_lab_evaluation_converts_only_supported_lab_units() -> None:
    criterion = _criterion(
        {
            "kind": "numeric_lab_threshold",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "comparator": ">=",
            "threshold": 8.0,
            "unit": "mmol/L",
        }
    )
    high = _fact(
        fact_id="lab-high",
        kind="observation",
        code=ClinicalCode(system="http://loinc.org", value="2345-7"),
        value=ObservationFactValue(numeric_value=9.0),
        unit="mmol/L",
    )
    low = _fact(
        fact_id="lab-low",
        kind="observation",
        code=ClinicalCode(system="http://loinc.org", value="2345-7"),
        value=ObservationFactValue(numeric_value=7.0),
        unit="mmol/L",
    )
    converted_unit = _fact(
        fact_id="lab-wrong-unit",
        kind="observation",
        code=ClinicalCode(system="http://loinc.org", value="2345-7"),
        value=ObservationFactValue(numeric_value=144.0),
        unit="mg/dL",
    )

    matched = evaluate_atomic_criterion(criterion, [high], as_of=date(2026, 8, 23))
    conflicting = evaluate_atomic_criterion(
        criterion, [high, low], as_of=date(2026, 8, 23)
    )
    incompatible_unit = evaluate_atomic_criterion(
        criterion, [converted_unit], as_of=date(2026, 8, 23)
    )

    assert matched.outcome == "met"
    assert conflicting.outcome == "conflicting"
    assert conflicting.evidence_fact_ids == ["lab-high", "lab-low"]
    assert incompatible_unit.outcome == "not_met"
    assert incompatible_unit.reason == "predicate_not_matched"
