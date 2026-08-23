"""Contract coverage for match-run lifecycle controls."""

import pytest

from app.main import app


@pytest.mark.contract
def test_openapi_contract_documents_match_run_cancellation() -> None:
    """Keep cancellation and safe terminal failure payloads in the public contract."""
    specification = app.openapi()
    cancel_operation = specification["paths"]["/match-runs/{run_id}/cancel"]["post"]

    assert "MatchRunFailureResponse" in specification["components"]["schemas"]
    assert cancel_operation["operationId"] == "cancel_match_run"
    assert cancel_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/MatchRunResponse"}
    assert cancel_operation["responses"]["404"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert cancel_operation["responses"]["409"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
