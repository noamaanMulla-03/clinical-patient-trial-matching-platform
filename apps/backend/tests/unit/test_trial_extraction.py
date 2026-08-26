"""Checks for deterministic ClinicalTrials.gov v2 searchable-field extraction."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from src.services.source_snapshots import trial_matching_source_hash
from src.trials.extraction import (
    TrialExtractionError,
    extract_source_update_time,
    extract_trial_fields,
)


def test_extracts_matching_relevant_fields_without_mutating_the_raw_study() -> None:
    study = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT01234567",
                "briefTitle": "A concise study title",
                "officialTitle": "A longer official title",
            },
            "statusModule": {"overallStatus": "RECRUITING"},
            "conditionsModule": {"conditions": ["Melanoma", "Lung Cancer"]},
            "designModule": {"phases": ["PHASE2", "PHASE3"]},
            "armsInterventionsModule": {
                "interventions": [
                    {
                        "name": "Study Drug",
                        "type": "DRUG",
                        "description": "Source-provided description",
                        "otherNames": ["SD-01"],
                    }
                ]
            },
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion Criteria:\n- Adult participants",
                "minimumAge": "18 Years",
                "maximumAge": "75 Years",
                "sex": "ALL",
            },
            "contactsLocationsModule": {
                "locations": [
                    {
                        "facility": "Example Hospital",
                        "status": "RECRUITING",
                        "city": "Boston",
                        "state": "Massachusetts",
                        "zip": "02115",
                        "country": "United States",
                    }
                ]
            },
        }
    }
    raw_snapshot = deepcopy(study)

    fields = extract_trial_fields(study)

    assert fields.nct_id == "NCT01234567"
    assert fields.title == "A concise study title"
    assert fields.conditions == ["Melanoma", "Lung Cancer"]
    assert fields.interventions[0].model_dump() == {
        "name": "Study Drug",
        "type": "DRUG",
        "description": "Source-provided description",
        "other_names": ["SD-01"],
    }
    assert fields.status == "RECRUITING"
    assert fields.phases == ["PHASE2", "PHASE3"]
    assert fields.eligibility_text == "Inclusion Criteria:\n- Adult participants"
    assert fields.minimum_age == "18 Years"
    assert fields.maximum_age == "75 Years"
    assert fields.sex == "ALL"
    assert fields.locations[0].model_dump() == {
        "facility": "Example Hospital",
        "status": "RECRUITING",
        "city": "Boston",
        "state": "Massachusetts",
        "postal_code": "02115",
        "country": "United States",
    }
    assert study == raw_snapshot

    irrelevant_change = deepcopy(study)
    irrelevant_change["protocolSection"]["identificationModule"]["organization"] = {
        "fullName": "Changed sponsor metadata"
    }
    eligibility_change = deepcopy(study)
    eligibility_change["protocolSection"]["eligibilityModule"][
        "eligibilityCriteria"
    ] = "Inclusion Criteria:\n- Adult participants\n- Another requirement"

    assert trial_matching_source_hash(fields) == trial_matching_source_hash(
        extract_trial_fields(irrelevant_change)
    )
    assert trial_matching_source_hash(fields) != trial_matching_source_hash(
        extract_trial_fields(eligibility_change)
    )


def test_missing_optional_modules_remain_explicitly_empty_or_absent() -> None:
    fields = extract_trial_fields(
        {"protocolSection": {"identificationModule": {"nctId": "NCT01234567"}}}
    )

    assert fields.title is None
    assert fields.conditions == []
    assert fields.interventions == []
    assert fields.status is None
    assert fields.phases == []
    assert fields.eligibility_text is None
    assert fields.minimum_age is None
    assert fields.maximum_age is None
    assert fields.sex is None
    assert fields.locations == []


def test_source_update_time_keeps_missing_and_invalid_values_explicit() -> None:
    available = extract_source_update_time(
        {
            "protocolSection": {
                "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-08-01"}}
            }
        }
    )
    missing = extract_source_update_time({"protocolSection": {}})
    invalid = extract_source_update_time(
        {
            "protocolSection": {
                "statusModule": {"lastUpdatePostDateStruct": {"date": "not-a-date"}}
            }
        }
    )

    assert available.value == datetime(2026, 8, 1, tzinfo=UTC)
    assert available.state == "available"
    assert missing.value is None and missing.state == "missing"
    assert invalid.value is None and invalid.state == "invalid"


@pytest.mark.parametrize(
    "study",
    [
        {"protocolSection": {"identificationModule": {"nctId": "NCT123"}}},
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT01234567"},
                "conditionsModule": {"conditions": "Melanoma"},
            }
        },
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT01234567"},
                "armsInterventionsModule": {"interventions": [{"type": "DRUG"}]},
            }
        },
    ],
)
def test_rejects_malformed_present_source_fields(study: dict[str, object]) -> None:
    with pytest.raises(TrialExtractionError):
        extract_trial_fields(study)
