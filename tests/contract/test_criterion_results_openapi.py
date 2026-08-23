"""Contract coverage for criterion detail and append-only correction routes."""

import pytest

from app.main import app


@pytest.mark.contract
def test_openapi_contract_documents_criterion_review_routes() -> None:
    """Publish source-linked evidence and correction response contracts."""
    specification = app.openapi()
    detail_path = "/criterion-results/{criterion_result_id}"
    detail_operation = specification["paths"][detail_path]["get"]
    correction_operation = specification["paths"][
        "/criterion-results/{criterion_result_id}/corrections"
    ]["post"]

    assert set(specification["components"]["schemas"]) >= {
        "CriterionDetailResponse",
        "ReviewCorrectionRequest",
        "ReviewCorrectionResponse",
    }
    assert detail_operation["operationId"] == "get_criterion_result_detail"
    assert detail_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/CriterionDetailResponse"}
    assert correction_operation["operationId"] == "append_criterion_reviewer_correction"
    assert correction_operation["responses"]["201"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ReviewCorrectionResponse"}
