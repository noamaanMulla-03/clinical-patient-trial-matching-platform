"""Contract coverage for the asynchronous match-run API."""

import pytest

from src.main import app


@pytest.mark.contract
def test_openapi_contract_documents_match_run_routes() -> None:
    """Publish the queue, status, and persisted-candidate result contracts."""
    specification = app.openapi()
    create_operation = specification["paths"]["/match-runs"]["post"]
    status_operation = specification["paths"]["/match-runs/{run_id}"]["get"]
    results_operation = specification["paths"]["/match-runs/{run_id}/results"]["get"]

    assert set(specification["components"]["schemas"]) >= {
        "MatchRunCreateRequest",
        "MatchRunResponse",
        "TrialMatchResponse",
    }
    assert create_operation["operationId"] == "create_match_run"
    assert create_operation["responses"]["202"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/MatchRunResponse"}
    assert create_operation["responses"]["422"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert status_operation["operationId"] == "get_match_run"
    assert status_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/MatchRunResponse"}
    assert status_operation["responses"]["404"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert results_operation["operationId"] == "get_match_run_results"
    assert results_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {
        "items": {"$ref": "#/components/schemas/TrialMatchResponse"},
        "title": "Response Get Match Run Results",
        "type": "array",
    }
    assert results_operation["responses"]["404"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
