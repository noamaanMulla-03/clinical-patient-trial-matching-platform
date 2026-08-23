"""Unit checks for bounded, explicit ClinicalTrials.gov job selections."""

import pytest

from app.workers.trial_ingestion import (
    MAX_TRIAL_INGESTION_PAGES,
    TrialIngestionJobError,
    TrialIngestionRequest,
)


@pytest.mark.parametrize(
    "request_kwargs", [{"query_term": "  lung cancer  "}, {"condition": "melanoma"}]
)
def test_query_or_condition_defaults_to_one_page(
    request_kwargs: dict[str, str],
) -> None:
    request = TrialIngestionRequest(**request_kwargs)

    assert request.query_term == "lung cancer" or request.condition == "melanoma"
    assert request.start_page == 1
    assert request.end_page == 1
    assert request.mode == "search"


def test_condition_and_query_can_be_combined_in_one_bounded_search() -> None:
    request = TrialIngestionRequest(
        query_term="immunotherapy",
        condition="melanoma",
        start_page=2,
        end_page=3,
    )

    assert request.mode == "search"
    assert request.start_page == 2
    assert request.end_page == 3


def test_explicit_page_range_allows_a_deliberate_bounded_catalog_selection() -> None:
    request = TrialIngestionRequest(start_page=1, end_page=MAX_TRIAL_INGESTION_PAGES)

    assert request.mode == "page_range"


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"nct_id": "NCT01234567", "condition": "melanoma"},
        {"nct_id": "NCT01234567", "start_page": 1, "end_page": 1},
        {"start_page": 2},
        {"start_page": 3, "end_page": 2},
        {"start_page": 1, "end_page": MAX_TRIAL_INGESTION_PAGES + 1},
        {"start_page": 2, "end_page": MAX_TRIAL_INGESTION_PAGES + 1},
        {"nct_id": "NCT123"},
        {"condition": "  "},
    ],
)
def test_request_rejects_ambiguous_or_unbounded_selections(
    request_kwargs: dict[str, object],
) -> None:
    with pytest.raises(TrialIngestionJobError):
        TrialIngestionRequest(**request_kwargs)  # type: ignore[arg-type]
