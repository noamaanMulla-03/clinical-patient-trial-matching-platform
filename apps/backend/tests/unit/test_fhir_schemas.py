"""Unit tests for FHIR import API contracts."""

import pytest
from pydantic import ValidationError

from src.fhir.safety import synthetic_data_tag
from src.fhir.schemas import (
    FHIR_IMPORT_INVALID_BUNDLE_CODE,
    ClinicalCode,
    FHIRImportRequest,
    FHIRProvenance,
    PatientFact,
)


def test_fhir_import_request_accepts_a_synthetically_marked_bundle() -> None:
    bundle = {
        "resourceType": "Bundle",
        "meta": {"tag": [synthetic_data_tag()]},
        "entry": [],
    }

    request = FHIRImportRequest(bundle=bundle)

    assert request.bundle == bundle


def test_fhir_import_request_rejects_an_unmarked_bundle_without_echoing_content() -> (
    None
):
    with pytest.raises(ValidationError) as caught_error:
        FHIRImportRequest(
            bundle={
                "resourceType": "Bundle",
                "entry": [{"resource": {"resourceType": "Patient", "name": "Test"}}],
            }
        )

    issue = caught_error.value.errors(include_input=False)[0]
    assert issue["type"] == FHIR_IMPORT_INVALID_BUNDLE_CODE
    assert issue["loc"] == ("bundle",)
    assert issue["msg"] == "FHIR import requires a synthetically marked Bundle."
    assert "input" not in issue


def test_patient_fact_preserves_clinical_value_time_code_and_fhir_provenance() -> None:
    fact = PatientFact(
        fact_id="fact-001",
        patient_id="synthetic-001",
        kind="observation",
        code=ClinicalCode(
            system="http://loinc.org",
            value="718-7",
            display="Hemoglobin",
        ),
        value=11.2,
        unit="g/dL",
        effective_at="2026-07-02T00:00:00Z",
        source=FHIRProvenance(
            resource_type="Observation",
            resource_id="obs-42",
            version_id="7",
        ),
        source_resource={
            "resourceType": "Observation",
            "id": "obs-42",
            "valueQuantity": {"value": 11.2, "unit": "g/dL"},
        },
    )

    assert fact.model_dump(mode="json") == {
        "fact_id": "fact-001",
        "patient_id": "synthetic-001",
        "kind": "observation",
        "code": {
            "system": "http://loinc.org",
            "value": "718-7",
            "display": "Hemoglobin",
        },
        "value": 11.2,
        "unit": "g/dL",
        "effective_at": "2026-07-02T00:00:00Z",
        "source": {
            "resource_type": "Observation",
            "resource_id": "obs-42",
            "version_id": "7",
        },
        "source_resource": {
            "resourceType": "Observation",
            "id": "obs-42",
            "valueQuantity": {"value": 11.2, "unit": "g/dL"},
        },
        "normalization": {"date": None, "quantity": None},
        "quality_issues": [],
    }


def test_patient_fact_allows_code_only_facts_without_inventing_a_value() -> None:
    fact = PatientFact(
        fact_id="fact-002",
        patient_id="synthetic-001",
        kind="condition",
        code=ClinicalCode(system="http://snomed.info/sct", value="44054006"),
        source=FHIRProvenance(resource_type="Condition", resource_id="condition-5"),
        source_resource={"resourceType": "Condition", "id": "condition-5"},
    )

    assert fact.value is None
    assert fact.unit is None
    assert fact.effective_at is None


def test_patient_fact_requires_traceable_supported_fhir_provenance() -> None:
    with pytest.raises(ValidationError) as caught_error:
        PatientFact(
            fact_id="fact-003",
            patient_id="synthetic-001",
            kind="observation",
            code=ClinicalCode(system="http://loinc.org", value="718-7"),
            source={"resource_type": "DiagnosticReport", "resource_id": "report-1"},
            source_resource={"resourceType": "DiagnosticReport", "id": "report-1"},
        )

    issue = caught_error.value.errors(include_input=False)[0]
    assert issue["type"] == "literal_error"
    assert issue["loc"] == ("source", "resource_type")
    assert "input" not in issue
