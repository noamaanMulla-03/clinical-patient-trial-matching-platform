"""Regression coverage for synthetic FHIR normalization edge-case fixtures."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from src.fhir.importer import normalize_patient_resource
from src.fhir.schemas import FHIRImportRequest

EDGE_FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "datasets" / "fhir-r4" / "edge-cases"
)
EVALUATED_AT = datetime(2025, 3, 1, tzinfo=UTC)


def _normalized_fixture(filename: str):
    bundle = json.loads((EDGE_FIXTURE_DIRECTORY / filename).read_text(encoding="utf-8"))
    FHIRImportRequest(bundle=bundle)
    return normalize_patient_resource(
        bundle,
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
        evaluated_at=EVALUATED_AT,
    )


def test_missing_date_fixture_marks_the_fact_unknown_for_time_based_review() -> None:
    normalized_patient = _normalized_fixture("missing-dates.json")

    assert len(normalized_patient.facts) == 3
    procedure_fact = next(
        fact for fact in normalized_patient.facts if fact.kind == "procedure"
    )
    assert procedure_fact.quality_issues[0].model_dump() == {
        "code": "missing",
        "field": "date",
        "message": "The source resource has no usable recorded date.",
    }


def test_multiple_lab_units_fixture_retains_each_measurement_without_conversion() -> (
    None
):
    normalized_patient = _normalized_fixture("multiple-lab-units.json")

    observations = [
        fact for fact in normalized_patient.facts if fact.kind == "observation"
    ]
    assert {fact.unit for fact in observations} == {"mg/dL", "mmol/L"}
    assert all(not fact.quality_issues for fact in observations)


def test_conflicting_labs_fixture_retains_both_values_and_marks_each_conflicting() -> (
    None
):
    normalized_patient = _normalized_fixture("conflicting-labs.json")

    observations = [
        fact for fact in normalized_patient.facts if fact.kind == "observation"
    ]
    assert [fact.value.numeric_value for fact in observations] == [11.2, 13.1]
    assert all(
        [issue.code for issue in fact.quality_issues] == ["conflicting"]
        for fact in observations
    )


def test_unknown_code_fixture_preserves_the_unmapped_source_coding() -> None:
    normalized_patient = _normalized_fixture("unknown-codes.json")

    observation_fact = next(
        fact for fact in normalized_patient.facts if fact.kind == "observation"
    )
    assert observation_fact.code.model_dump() == {
        "system": "urn:synthetic:unmapped-labs",
        "value": "local-lab-001",
        "display": None,
    }
    assert observation_fact.source_resource["code"] == {
        "coding": [{"system": "urn:synthetic:unmapped-labs", "code": "local-lab-001"}]
    }
