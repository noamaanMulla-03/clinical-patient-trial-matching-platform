"""Validation coverage for append-only criterion reviewer corrections."""

import pytest
from pydantic import ValidationError

from app.criteria.api_schemas import ReviewCorrectionRequest


def test_reviewer_correction_normalizes_identifiers_and_rejects_extras() -> None:
    """The correction boundary stores review metadata, not arbitrary request content."""
    request = ReviewCorrectionRequest.model_validate(
        {
            "reviewer_id": " reviewer-17 ",
            "corrected_outcome": "conflicting",
            "reason": " Evidence sources disagree. ",
        }
    )

    assert request.model_dump() == {
        "reviewer_id": "reviewer-17",
        "corrected_outcome": "conflicting",
        "reason": "Evidence sources disagree.",
    }
    with pytest.raises(ValidationError):
        ReviewCorrectionRequest.model_validate(
            {
                "reviewer_id": "reviewer-17",
                "corrected_outcome": "met",
                "reason": "Reason provided.",
                "unexpected": "value",
            }
        )
