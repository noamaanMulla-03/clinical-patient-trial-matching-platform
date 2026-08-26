"""Unit checks for safe, bounded trial-sync API request models."""

import pytest
from pydantic import ValidationError

from src.trials.schemas import TrialSyncCreateRequest


def test_trial_sync_request_uses_worker_normalization_for_a_search() -> None:
    """The public request and durable selection use the same normalized boundary."""
    request = TrialSyncCreateRequest(query_term="  melanoma  ", page_size=5)

    selection = request.to_ingestion_request()

    assert selection.query_term == "melanoma"
    assert selection.start_page == 1
    assert selection.end_page == 1
    assert selection.mode == "search"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"nct_id": "NCT123"},
        {"start_page": 1},
        {"start_page": 1, "end_page": 11},
        {"condition": "melanoma", "unexpected": "value"},
    ],
)
def test_trial_sync_request_rejects_unbounded_or_unknown_input(
    payload: dict[str, object],
) -> None:
    """Invalid input cannot create an unbounded background job."""
    with pytest.raises(ValidationError):
        TrialSyncCreateRequest.model_validate(payload)
