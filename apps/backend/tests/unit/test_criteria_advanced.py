"""Focused safety checks for advanced deterministic criterion rules."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.criteria.evaluation import evaluate_atomic_criterion
from src.criteria.schemas import AtomicCriterion
from src.fhir.schemas import (
    ClinicalCode,
    FHIRProvenance,
    MedicationFactValue,
    ObservationFactValue,
    PatientFact,
    ProcedureFactValue,
)


def _criterion(rule: dict[str, object]) -> AtomicCriterion:
    return AtomicCriterion(
        category="inclusion",
        source_text="criterion",
        source_start=0,
        source_end=9,
        rule=rule,
    )


def _fact(
    *,
    fact_id: str,
    kind: str,
    code: str,
    value: object,
    unit: str | None = None,
    effective_at: datetime | None = None,
) -> PatientFact:
    resource_type = {
        "observation": "Observation",
        "medication": "MedicationStatement",
        "procedure": "Procedure",
    }[kind]
    return PatientFact(
        fact_id=fact_id,
        patient_id="synthetic-patient",
        kind=kind,
        code=ClinicalCode(system="http://loinc.org", value=code),
        value=value,
        unit=unit,
        effective_at=effective_at,
        source=FHIRProvenance(resource_type=resource_type, resource_id=fact_id),
        source_resource={"resourceType": resource_type, "id": fact_id},
    )


def test_numeric_lab_conversion_is_limited_to_a_validated_exact_lab_code() -> None:
    criterion = _criterion(
        {
            "kind": "numeric_lab_threshold",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "comparator": ">=",
            "threshold": 7.5,
            "unit": "mmol/L",
        }
    )
    glucose = _fact(
        fact_id="glucose-mg-dl",
        kind="observation",
        code="2345-7",
        value=ObservationFactValue(numeric_value=144),
        unit="mg/dL",
    )

    result = evaluate_atomic_criterion(criterion, [glucose], as_of=date(2026, 8, 23))

    assert result.outcome == "met"
    with pytest.raises(ValidationError, match="supported conversion"):
        _criterion(
            {
                "kind": "numeric_lab_threshold",
                "code": {"system": "http://loinc.org", "value": "2345-7"},
                "comparator": ">=",
                "threshold": 7.5,
                "unit": "g/L",
            }
        )


def test_date_and_recency_rules_preserve_missing_and_conflicting_dates() -> None:
    fact = _fact(
        fact_id="recent-glucose",
        kind="observation",
        code="2345-7",
        value=ObservationFactValue(numeric_value=7.0),
        unit="mmol/L",
        effective_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    date_rule = _criterion(
        {
            "kind": "date_window",
            "fact_kind": "observation",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "starts_on": "2026-08-01",
            "ends_on": "2026-08-31",
        }
    )
    recency_rule = _criterion(
        {
            "kind": "recency_window",
            "fact_kind": "observation",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "within_days": 30,
        }
    )

    assert (
        evaluate_atomic_criterion(date_rule, [fact], as_of=date(2026, 8, 23)).outcome
        == "met"
    )
    assert (
        evaluate_atomic_criterion(recency_rule, [fact], as_of=date(2026, 8, 23)).outcome
        == "met"
    )
    assert (
        evaluate_atomic_criterion(
            recency_rule,
            [
                fact,
                fact.model_copy(
                    update={
                        "fact_id": "older",
                        "effective_at": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                ),
            ],
            as_of=date(2026, 8, 23),
        ).outcome
        == "conflicting"
    )


def test_medication_and_procedure_checks_require_documented_statuses() -> None:
    medication = _fact(
        fact_id="medication",
        kind="medication",
        code="12345",
        value=MedicationFactValue(status="active"),
    )
    procedure = _fact(
        fact_id="procedure",
        kind="procedure",
        code="67890",
        value=ProcedureFactValue(status="completed"),
    )
    medication_rule = _criterion(
        {
            "kind": "medication_status",
            "code": {"system": "http://loinc.org", "value": "12345"},
            "expected_status": "active",
        }
    )
    procedure_rule = _criterion(
        {
            "kind": "procedure_status",
            "code": {"system": "http://loinc.org", "value": "67890"},
            "expected_status": "completed",
        }
    )

    assert (
        evaluate_atomic_criterion(
            medication_rule, [medication], as_of=date(2026, 8, 23)
        ).outcome
        == "met"
    )
    assert (
        evaluate_atomic_criterion(
            procedure_rule, [procedure], as_of=date(2026, 8, 23)
        ).outcome
        == "met"
    )
    undocumented = medication.model_copy(
        update={"value": MedicationFactValue(status=None)}
    )
    result = evaluate_atomic_criterion(
        medication_rule, [undocumented], as_of=date(2026, 8, 23)
    )
    assert result.outcome == "unknown"
    assert result.reason == "undocumented_status"
