"""Conservative patient-derived trial-retrieval query and metadata filter checks."""

from datetime import date

from src.fhir.schemas import (
    ClinicalCode,
    ConditionFactValue,
    FHIRProvenance,
    MedicationFactValue,
    PatientFact,
    ProcedureFactValue,
)
from src.retrieval.filtering import trial_matches_metadata
from src.retrieval.query_builder import build_patient_retrieval_query
from src.retrieval.schemas import TrialMetadataFilters, TrialSearchMetadata


def _fact(
    *,
    fact_id: str,
    kind: str,
    code: str,
    display: str | None,
    value: object,
    quality_issues: list[dict[str, str]] | None = None,
) -> PatientFact:
    resource_type = {
        "demographic": "Patient",
        "condition": "Condition",
        "medication": "MedicationStatement",
        "procedure": "Procedure",
    }[kind]
    return PatientFact(
        fact_id=fact_id,
        patient_id="synthetic-patient",
        kind=kind,
        code=ClinicalCode(system="http://example.test", value=code, display=display),
        value=value,
        source=FHIRProvenance(resource_type=resource_type, resource_id=fact_id),
        source_resource={"resourceType": resource_type, "id": fact_id},
        quality_issues=quality_issues or [],
    )


def test_patient_query_uses_only_active_documented_facts_and_exact_demographics() -> (
    None
):
    query = build_patient_retrieval_query(
        [
            _fact(
                fact_id="condition",
                kind="condition",
                code="44054006",
                display="Diabetes mellitus",
                value=ConditionFactValue(clinical_status="active"),
            ),
            _fact(
                fact_id="medication",
                kind="medication",
                code="860975",
                display="Metformin",
                value=MedicationFactValue(status="active"),
            ),
            _fact(
                fact_id="procedure",
                kind="procedure",
                code="80146002",
                display="Appendectomy",
                value=ProcedureFactValue(status="completed"),
            ),
            _fact(
                fact_id="birth-date",
                kind="demographic",
                code="birth-date",
                display=None,
                value="1980-08-23",
            ),
            _fact(
                fact_id="sex",
                kind="demographic",
                code="administrative-gender",
                display=None,
                value="female",
            ),
        ],
        as_of=date(2026, 8, 23),
    )

    assert query.lexical_text == "Diabetes mellitus Metformin Appendectomy"
    assert [term.source_fact_id for term in query.terms] == [
        "condition",
        "medication",
        "procedure",
    ]
    assert query.filters.model_dump() == {
        "conditions": ["Diabetes mellitus"],
        "age_years": 46,
        "recorded_sex": "female",
        "study_statuses": [],
        "phases": [],
        "countries": [],
        "intervention_types": [],
    }


def test_uncertain_patient_information_is_omitted_instead_of_becoming_a_filter() -> (
    None
):
    query = build_patient_retrieval_query(
        [
            _fact(
                fact_id="stale-condition",
                kind="condition",
                code="44054006",
                display="Diabetes mellitus",
                value=ConditionFactValue(clinical_status="active"),
                quality_issues=[
                    {"code": "stale", "field": "recordedDate", "message": "Stale."}
                ],
            ),
            _fact(
                fact_id="partial-birth-date",
                kind="demographic",
                code="birth-date",
                display=None,
                value="1980",
            ),
            _fact(
                fact_id="female",
                kind="demographic",
                code="administrative-gender",
                display=None,
                value="female",
            ),
            _fact(
                fact_id="male",
                kind="demographic",
                code="administrative-gender",
                display=None,
                value="male",
            ),
        ],
        as_of=date(2026, 8, 23),
    )

    assert query.terms == []
    assert query.filters.conditions == []
    assert query.filters.age_years is None
    assert query.filters.recorded_sex is None


def test_metadata_filters_reject_only_documented_patient_mismatches() -> None:
    metadata = TrialSearchMetadata(
        conditions=["Diabetes mellitus"],
        minimum_age="18 Years",
        maximum_age="70 Years",
        sex="FEMALE",
        status="RECRUITING",
        phases=["PHASE2"],
        countries=["India"],
        intervention_types=["DRUG"],
    )
    filters = TrialMetadataFilters(
        conditions=["diabetes mellitus"],
        age_years=46,
        recorded_sex="female",
        study_statuses=["recruiting"],
        phases=["phase2"],
        countries=["india"],
        intervention_types=["drug"],
    )

    assert trial_matches_metadata(metadata, filters)
    assert not trial_matches_metadata(
        metadata, filters.model_copy(update={"age_years": 71})
    )
    assert not trial_matches_metadata(
        metadata, filters.model_copy(update={"conditions": ["melanoma"]})
    )
    assert not trial_matches_metadata(
        metadata, filters.model_copy(update={"study_statuses": ["completed"]})
    )

    uncertain_trial_metadata = TrialSearchMetadata()
    patient_only_filters = TrialMetadataFilters(
        conditions=["Diabetes mellitus"], age_years=46, recorded_sex="female"
    )
    assert trial_matches_metadata(uncertain_trial_metadata, patient_only_filters)
    assert not trial_matches_metadata(
        uncertain_trial_metadata,
        patient_only_filters.model_copy(update={"countries": ["India"]}),
    )
