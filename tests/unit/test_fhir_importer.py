"""Unit tests for minimal source-preserving Patient normalization."""

from datetime import UTC, datetime
from uuid import UUID

from app.fhir.importer import normalize_patient_resource
from app.fhir.schemas import (
    AllergyFactValue,
    ConditionFactValue,
    MedicationFactValue,
    ObservationFactValue,
    ProcedureFactValue,
)


def test_normalizes_recorded_patient_demographics_and_partial_birth_date() -> None:
    """Keep names in the source Bundle; normalize only matching-relevant facts."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "synthetic-001",
                        "name": [{"family": "Synthetic", "given": ["Patient"]}],
                        "gender": "female",
                        "birthDate": "1990",
                        "meta": {"versionId": "7"},
                    }
                }
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert normalized_patient.patient_id == "synthetic-001"
    assert [
        (fact.kind, fact.code.value, fact.value, fact.effective_at)
        for fact in normalized_patient.facts
    ] == [
        ("demographic", "administrative-gender", "female", None),
        ("demographic", "birth-date", "1990", None),
    ]
    assert all(
        fact.source.resource_type == "Patient" for fact in normalized_patient.facts
    )
    assert all(
        fact.source.resource_id == "synthetic-001" for fact in normalized_patient.facts
    )
    assert all(fact.source.version_id == "7" for fact in normalized_patient.facts)


def test_normalizes_condition_code_status_and_onset_for_the_bundle_patient() -> None:
    """Keep each condition tied to its coded source and explicit Patient reference."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "condition-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": "44054006",
                                    "display": "Diabetes mellitus type 2",
                                }
                            ]
                        },
                        "clinicalStatus": {"coding": [{"code": "active"}]},
                        "onsetDateTime": "2020-01-15",
                        "meta": {"versionId": "3"},
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert len(normalized_patient.facts) == 1
    condition_fact = normalized_patient.facts[0]
    assert condition_fact.kind == "condition"
    assert condition_fact.code.model_dump() == {
        "system": "http://snomed.info/sct",
        "value": "44054006",
        "display": "Diabetes mellitus type 2",
    }
    assert condition_fact.value == ConditionFactValue(
        clinical_status="active", onset_date="2020-01-15"
    )
    assert condition_fact.source.model_dump() == {
        "resource_type": "Condition",
        "resource_id": "condition-001",
        "version_id": "3",
    }


def test_normalizes_numeric_observation_with_its_recorded_range_and_time() -> None:
    """A numeric result retains its source unit, range, status, and exact date text."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "observation-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {
                                    "system": "http://loinc.org",
                                    "code": "2345-7",
                                    "display": "Glucose",
                                }
                            ]
                        },
                        "valueQuantity": {"value": 7.2, "unit": "mmol/L"},
                        "referenceRange": [
                            {
                                "low": {"value": 3.9, "unit": "mmol/L"},
                                "high": {"value": 5.5, "unit": "mmol/L"},
                                "text": "Fasting reference interval",
                            }
                        ],
                        "status": "final",
                        "effectiveDateTime": "2020-01-15T12:30:00Z",
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert len(normalized_patient.facts) == 1
    observation_fact = normalized_patient.facts[0]
    assert observation_fact.kind == "observation"
    assert observation_fact.unit == "mmol/L"
    assert observation_fact.value == ObservationFactValue.model_validate(
        {
            "numeric_value": 7.2,
            "status": "final",
            "effective_date": "2020-01-15T12:30:00Z",
            "reference_ranges": [
                {
                    "low": {"value": 3.9, "unit": "mmol/L"},
                    "high": {"value": 5.5, "unit": "mmol/L"},
                    "text": "Fasting reference interval",
                }
            ],
        }
    )
    assert observation_fact.effective_at == datetime(2020, 1, 15, 12, 30, tzinfo=UTC)
    assert observation_fact.source.resource_type == "Observation"


def test_normalizes_medications_procedure_and_allergy_as_source_linked_facts() -> None:
    """Each resource type remains distinct while sharing the Patient source."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "MedicationStatement",
                        "id": "medication-statement-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "medicationCodeableConcept": {
                            "coding": [{"system": "http://rxnorm", "code": "860975"}]
                        },
                        "status": "active",
                        "effectiveDateTime": "2020-01-15T12:30:00+00:00",
                    }
                },
                {
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "id": "medication-request-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "medicationCodeableConcept": {
                            "coding": [{"system": "http://rxnorm", "code": "197361"}]
                        },
                        "status": "active",
                        "intent": "order",
                        "authoredOn": "2020-01-16T12:30:00Z",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Procedure",
                        "id": "procedure-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {"system": "http://snomed.info/sct", "code": "80146002"}
                            ]
                        },
                        "status": "completed",
                        "performedPeriod": {"start": "2020-01-17T12:30:00Z"},
                    }
                },
                {
                    "resource": {
                        "resourceType": "AllergyIntolerance",
                        "id": "allergy-001",
                        "patient": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [{"system": "http://rxnorm", "code": "7980"}]
                        },
                        "clinicalStatus": {"coding": [{"code": "active"}]},
                        "verificationStatus": {"coding": [{"code": "confirmed"}]},
                        "recordedDate": "2020-01-18T12:30:00Z",
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    facts_by_source = {
        fact.source.resource_type: fact for fact in normalized_patient.facts
    }
    assert facts_by_source["MedicationStatement"].value == MedicationFactValue(
        status="active", intent=None, effective_date="2020-01-15T12:30:00+00:00"
    )
    assert facts_by_source["MedicationRequest"].value == MedicationFactValue(
        status="active", intent="order", effective_date="2020-01-16T12:30:00Z"
    )
    assert facts_by_source["Procedure"].value == ProcedureFactValue(
        status="completed", performed_date="2020-01-17T12:30:00Z"
    )
    assert facts_by_source["AllergyIntolerance"].value == AllergyFactValue(
        clinical_status="active",
        verification_status="confirmed",
        recorded_date="2020-01-18T12:30:00Z",
    )
    assert {fact.kind for fact in facts_by_source.values()} == {
        "medication",
        "procedure",
        "allergy",
    }


def test_normalizes_coding_date_and_quantity_without_changing_source_evidence() -> None:
    """Clean matching fields while retaining the original FHIR resource verbatim."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "observation-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {
                                    "system": " http://loinc.org ",
                                    "code": " 718-7 ",
                                    "display": " Hemoglobin   concentration ",
                                }
                            ]
                        },
                        "valueQuantity": {
                            "value": 11.2,
                            "unit": " g/dL ",
                            "system": " http://unitsofmeasure.org ",
                            "code": " g/dL ",
                        },
                        "status": "final",
                        "effectiveDateTime": "2020-01-15T12:30:00-05:00",
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
        evaluated_at=datetime(2020, 1, 16, tzinfo=UTC),
    )

    observation_fact = normalized_patient.facts[0]
    assert observation_fact.code.model_dump() == {
        "system": "http://loinc.org",
        "value": "718-7",
        "display": "Hemoglobin concentration",
    }
    assert observation_fact.unit == "g/dL"
    assert observation_fact.normalization.model_dump(mode="json") == {
        "date": {
            "source_value": "2020-01-15T12:30:00-05:00",
            "precision": "datetime",
            "normalized_date": "2020-01-15",
            "normalized_at": "2020-01-15T17:30:00Z",
        },
        "quantity": {
            "value": 11.2,
            "unit": "g/dL",
            "system": "http://unitsofmeasure.org",
            "code": "g/dL",
        },
    }
    assert observation_fact.source_resource["code"]["coding"][0] == {
        "system": " http://loinc.org ",
        "code": " 718-7 ",
        "display": " Hemoglobin   concentration ",
    }


def test_preserves_conflicting_and_unusable_observations_as_explicit_quality_data() -> (
    None
):
    """Conflicts stay as separate facts; unusable values become import issues."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "observation-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "718-7"}]
                        },
                        "valueQuantity": {"value": 11.2, "unit": "g/dL"},
                        "status": "final",
                        "effectiveDateTime": "2020-01-15",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "observation-002",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "718-7"}]
                        },
                        "valueQuantity": {"value": 12.4, "unit": "g/dL"},
                        "status": "final",
                        "effectiveDateTime": "2020-01-15",
                    }
                },
                {
                    "resource": {
                        "resourceType": "Observation",
                        "id": "observation-003",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [{"system": "http://loinc.org", "code": "718-7"}]
                        },
                        "valueQuantity": {"value": "not-a-number"},
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
        evaluated_at=datetime(2020, 1, 16, tzinfo=UTC),
    )

    observation_facts = [
        fact for fact in normalized_patient.facts if fact.kind == "observation"
    ]
    assert [fact.value.numeric_value for fact in observation_facts] == [11.2, 12.4]
    assert all(
        {issue.code for issue in fact.quality_issues} == {"conflicting"}
        for fact in observation_facts
    )
    assert [issue.model_dump() for issue in normalized_patient.data_quality_issues] == [
        {
            "code": "missing",
            "field": "gender",
            "message": "Patient gender is absent from the source resource.",
            "source": {
                "resource_type": "Patient",
                "resource_id": "synthetic-001",
                "version_id": None,
            },
            "fact_id": None,
        },
        {
            "code": "missing",
            "field": "birthDate",
            "message": "Patient birthDate is absent from the source resource.",
            "source": {
                "resource_type": "Patient",
                "resource_id": "synthetic-001",
                "version_id": None,
            },
            "fact_id": None,
        },
        {
            "code": "invalid",
            "field": "valueQuantity.value",
            "message": "Observation lacks a usable numeric valueQuantity.value.",
            "source": {
                "resource_type": "Observation",
                "resource_id": "observation-003",
                "version_id": None,
            },
            "fact_id": None,
        },
        {
            "code": "conflicting",
            "field": "valueQuantity.value",
            "message": (
                "Another Observation has the same code and effective date "
                "with a different numeric value."
            ),
            "source": {
                "resource_type": "Observation",
                "resource_id": "observation-001",
                "version_id": None,
            },
            "fact_id": (
                "00000000-0000-0000-0000-000000000001:Observation:"
                "observation-001:coding-0"
            ),
        },
        {
            "code": "conflicting",
            "field": "valueQuantity.value",
            "message": (
                "Another Observation has the same code and effective date "
                "with a different numeric value."
            ),
            "source": {
                "resource_type": "Observation",
                "resource_id": "observation-002",
                "version_id": None,
            },
            "fact_id": (
                "00000000-0000-0000-0000-000000000001:Observation:"
                "observation-002:coding-0"
            ),
        },
    ]


def test_marks_old_source_evidence_stale_without_discarding_the_fact() -> None:
    """Freshness is a review signal, not a reason to erase historic evidence."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Condition",
                        "id": "condition-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {"system": "http://snomed.info/sct", "code": "44054006"}
                            ]
                        },
                        "onsetDateTime": "2020-01-15",
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
        evaluated_at=datetime(2021, 1, 16, tzinfo=UTC),
    )

    assert len(normalized_patient.facts) == 1
    assert normalized_patient.facts[0].quality_issues[0].code == "stale"
    assert normalized_patient.data_quality_issues[-1].code == "stale"


def test_marks_invalid_fhir_dates_without_discarding_source_evidence() -> None:
    """An invalid date becomes explicit quality metadata rather than a guessed time."""
    normalized_patient = normalize_patient_resource(
        {
            "resourceType": "Bundle",
            "entry": [
                {
                    "fullUrl": "urn:uuid:synthetic-001",
                    "resource": {"resourceType": "Patient", "id": "synthetic-001"},
                },
                {
                    "resource": {
                        "resourceType": "Procedure",
                        "id": "procedure-001",
                        "subject": {"reference": "urn:uuid:synthetic-001"},
                        "code": {
                            "coding": [
                                {"system": "http://snomed.info/sct", "code": "80146002"}
                            ]
                        },
                        "performedDateTime": "2020-15-45",
                    }
                },
            ],
        },
        patient_import_id=UUID("00000000-0000-0000-0000-000000000001"),
        evaluated_at=datetime(2020, 1, 16, tzinfo=UTC),
    )

    procedure_fact = normalized_patient.facts[0]
    assert procedure_fact.normalization.date is None
    assert procedure_fact.quality_issues[0].code == "invalid"
    assert normalized_patient.data_quality_issues[-1].field == "date"
