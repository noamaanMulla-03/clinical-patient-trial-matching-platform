"""Adversarial safety checks for deterministic clinical criterion evaluation."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.criteria.evaluation import evaluate_atomic_criterion
from app.criteria.schemas import AtomicCriterion, CriterionEvaluation
from app.fhir.schemas import (
    ClinicalCode,
    FHIRProvenance,
    MedicationFactValue,
    ObservationFactValue,
    PatientFact,
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
    effective_at: datetime | None = None,
) -> PatientFact:
    resource_type = "MedicationStatement" if kind == "medication" else "Observation"
    return PatientFact(
        fact_id=fact_id,
        patient_id="synthetic-patient",
        kind=kind,
        code=ClinicalCode(system="http://loinc.org", value=code),
        value=value,
        effective_at=effective_at,
        source=FHIRProvenance(resource_type=resource_type, resource_id=fact_id),
        source_resource={"resourceType": resource_type, "id": fact_id},
    )


def test_malformed_rules_and_resolved_results_without_evidence_are_rejected() -> None:
    with pytest.raises(ValidationError, match="supported conversion"):
        _criterion(
            {
                "kind": "numeric_lab_threshold",
                "code": {"system": "http://loinc.org", "value": "99999-9"},
                "comparator": ">=",
                "threshold": 1.0,
                "unit": "mmol/L",
            }
        )
    with pytest.raises(ValidationError, match="require evidence IDs"):
        CriterionEvaluation(
            outcome="met",
            evidence_fact_ids=[],
            reason="predicate_matched",
            requires_review=False,
        )


def test_future_or_missing_event_dates_never_become_resolved_results() -> None:
    criterion = _criterion(
        {
            "kind": "recency_window",
            "fact_kind": "observation",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "within_days": 30,
        }
    )
    future = _fact(
        fact_id="future",
        kind="observation",
        code="2345-7",
        value=ObservationFactValue(numeric_value=7.0),
        effective_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    missing = future.model_copy(update={"fact_id": "missing", "effective_at": None})

    future_result = evaluate_atomic_criterion(
        criterion, [future], as_of=date(2026, 8, 23)
    )
    missing_result = evaluate_atomic_criterion(
        criterion, [missing], as_of=date(2026, 8, 23)
    )

    assert (future_result.outcome, future_result.reason) == ("unknown", "future_date")
    assert (missing_result.outcome, missing_result.reason) == (
        "unknown",
        "missing_date",
    )


def test_conflicting_documented_medication_statuses_remain_conflicting() -> None:
    criterion = _criterion(
        {
            "kind": "medication_status",
            "code": {"system": "http://loinc.org", "value": "12345"},
            "expected_status": "active",
        }
    )
    active = _fact(
        fact_id="medication-active",
        kind="medication",
        code="12345",
        value=MedicationFactValue(status="active"),
    )
    stopped = _fact(
        fact_id="medication-stopped",
        kind="medication",
        code="12345",
        value=MedicationFactValue(status="stopped"),
    )

    result = evaluate_atomic_criterion(
        criterion, [active, stopped], as_of=date(2026, 8, 23)
    )

    assert result.outcome == "conflicting"
    assert result.requires_review is True
