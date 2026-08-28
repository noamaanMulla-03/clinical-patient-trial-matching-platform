"""Checks for immutable source-version retrieval views."""

from uuid import uuid4

from src.db.models import TrialVersion
from src.retrieval.trial_documents import document_from_trial_version


def test_document_uses_exact_trial_version_source_fields() -> None:
    version = TrialVersion(
        id=uuid4(),
        nct_id="NCT00000001",
        raw_study={
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00000001",
                    "briefTitle": "Immutable public study",
                },
                "conditionsModule": {"conditions": ["Diabetes mellitus"]},
                "eligibilityModule": {"eligibilityCriteria": "Adults only"},
            }
        },
    )

    document = document_from_trial_version(version)

    assert document.trial_version_id == version.id
    assert document.nct_id == "NCT00000001"
    assert document.title == "Immutable public study"
    assert document.conditions == ["Diabetes mellitus"]
    assert document.eligibility_text == "Adults only"
