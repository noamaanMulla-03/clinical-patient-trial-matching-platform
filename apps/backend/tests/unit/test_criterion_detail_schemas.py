"""Validation coverage for append-only criterion reviewer corrections."""

import pytest
from pydantic import ValidationError

from src.criteria.api_schemas import ReviewCorrectionRequest


def test_reviewer_correction_requires_a_controlled_reason_code_and_rejects_extras() -> (
    None
):
    """The correction boundary does not accept reviewer identity or free text."""
    request = ReviewCorrectionRequest.model_validate(
        {
            "corrected_outcome": "conflicting",
            "reason_code": "evidence_conflicting",
        }
    )

    assert request.model_dump() == {
        "corrected_outcome": "conflicting",
        "reason_code": "evidence_conflicting",
    }
    with pytest.raises(ValidationError):
        ReviewCorrectionRequest.model_validate(
            {
                "corrected_outcome": "met",
                "reason_code": "evidence_missing",
                "unexpected": "value",
            }
        )
    with pytest.raises(ValidationError):
        ReviewCorrectionRequest.model_validate(
            {
                "corrected_outcome": "met",
                "reason_code": "Patient reported a new diagnosis.",
            }
        )
