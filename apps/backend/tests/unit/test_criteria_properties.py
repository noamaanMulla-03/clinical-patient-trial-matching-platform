"""Property-based checks for deterministic laboratory conversion behavior."""

from datetime import date
from math import isclose

from hypothesis import given, settings
from hypothesis import strategies as st

from src.criteria.evaluation import evaluate_atomic_criterion
from src.criteria.schemas import AtomicCriterion
from src.criteria.units import convert_lab_value
from src.fhir.schemas import (
    ClinicalCode,
    FHIRProvenance,
    ObservationFactValue,
    PatientFact,
)

_FINITE_LAB_VALUES = st.floats(
    min_value=0.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)


@settings(max_examples=100)
@given(value=_FINITE_LAB_VALUES)
def test_glucose_conversion_round_trips_without_changing_its_value(
    value: float,
) -> None:
    """A supported unit round-trip may not change the recorded numeric quantity."""
    mmol_per_liter = convert_lab_value(
        system="http://loinc.org",
        code="2345-7",
        value=value,
        source_unit="mg/dL",
        target_unit="mmol/L",
    )
    round_tripped = convert_lab_value(
        system="http://loinc.org",
        code="2345-7",
        value=mmol_per_liter,
        source_unit="mmol/L",
        target_unit="mg/dL",
    )

    assert isclose(round_tripped, value, rel_tol=1e-12, abs_tol=1e-12)


@settings(max_examples=100)
@given(value=_FINITE_LAB_VALUES)
def test_generated_supported_glucose_values_match_their_converted_threshold(
    value: float,
) -> None:
    """Evaluation must use the same explicit conversion as the threshold contract."""
    threshold = convert_lab_value(
        system="http://loinc.org",
        code="2345-7",
        value=value,
        source_unit="mg/dL",
        target_unit="mmol/L",
    )
    criterion = AtomicCriterion(
        category="inclusion",
        source_text="glucose",
        source_start=0,
        source_end=7,
        rule={
            "kind": "numeric_lab_threshold",
            "code": {"system": "http://loinc.org", "value": "2345-7"},
            "comparator": ">=",
            "threshold": threshold,
            "unit": "mmol/L",
        },
    )
    fact = PatientFact(
        fact_id="generated-glucose",
        patient_id="synthetic-patient",
        kind="observation",
        code=ClinicalCode(system="http://loinc.org", value="2345-7"),
        value=ObservationFactValue(numeric_value=value),
        unit="mg/dL",
        source=FHIRProvenance(resource_type="Observation", resource_id="glucose"),
        source_resource={"resourceType": "Observation", "id": "glucose"},
    )

    result = evaluate_atomic_criterion(criterion, [fact], as_of=date(2026, 8, 23))

    assert result.outcome == "met"
    assert result.evidence_fact_ids == ["generated-glucose"]
