"""Checks for the fixed, intentionally small development trial collection."""

from app.trials.development_collection import (
    DEVELOPMENT_TRIAL_COLLECTION,
    development_trial_ingestion_requests,
)


def test_development_collection_creates_fixed_nct_jobs_with_provenance() -> None:
    """A source-controlled ID list makes every development run reproducible."""
    requests = development_trial_ingestion_requests()

    assert DEVELOPMENT_TRIAL_COLLECTION.collection_id == "development-melanoma-v1"
    assert DEVELOPMENT_TRIAL_COLLECTION.nct_ids == (
        "NCT02434107",
        "NCT01610531",
        "NCT00849407",
    )
    assert tuple(request.nct_id for request in requests) == (
        DEVELOPMENT_TRIAL_COLLECTION.nct_ids
    )
    assert all(request.mode == "nct_id" for request in requests)
    assert all(
        request.collection_id == DEVELOPMENT_TRIAL_COLLECTION.collection_id
        for request in requests
    )
