"""Contract tests for the API surface available at the current build stage."""

import pytest

from app.main import app


@pytest.mark.contract
def test_openapi_contract_documents_the_core_system_routes() -> None:
    """Keep the published schema aligned with actual, safe runtime routes."""
    specification = app.openapi()
    health_operation = specification["paths"]["/health"]["get"]
    import_operation = specification["paths"]["/patients/import/fhir"]["post"]
    patient_operation = specification["paths"]["/patients/{patient_id}"]["get"]
    fact_source_operation = specification["paths"][
        "/patients/{patient_id}/facts/{fact_id}/source"
    ]["get"]
    create_trial_sync_operation = specification["paths"]["/trial-syncs"]["post"]
    get_trial_sync_operation = specification["paths"]["/trial-syncs/{job_id}"]["get"]

    assert app.openapi_url == "/openapi.json"
    assert app.docs_url == "/docs"
    assert specification["info"] == {
        "title": "Clinical Trial Patient-Matching Platform",
        "description": "Research-only decision support using synthetic data.",
        "version": "0.1.0",
    }
    assert health_operation["operationId"] == "health_check"
    assert health_operation["tags"] == ["system"]
    assert health_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/HealthResponse"}
    assert health_operation["responses"]["500"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert health_operation["responses"]["200"]["headers"]["X-Request-ID"][
        "schema"
    ] == {"type": "string", "minLength": 1, "maxLength": 128}
    assert set(specification["components"]["schemas"]) >= {
        "APIErrorDetail",
        "APIErrorResponse",
        "APIValidationIssue",
        "FHIRImportRequest",
        "FHIRImportResponse",
        "HealthResponse",
        "PatientTimelineResponse",
        "PatientFactSourceResponse",
        "TrialSyncCreateRequest",
        "TrialSyncResponse",
    }
    assert import_operation["operationId"] == "import_synthetic_fhir_bundle"
    assert import_operation["responses"]["201"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/FHIRImportResponse"}
    assert import_operation["responses"]["422"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert patient_operation["operationId"] == "get_synthetic_patient_timeline"
    assert patient_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PatientTimelineResponse"}
    assert patient_operation["responses"]["404"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert fact_source_operation["operationId"] == "get_synthetic_patient_fact_source"
    assert fact_source_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PatientFactSourceResponse"}
    assert create_trial_sync_operation["operationId"] == "create_trial_sync"
    assert create_trial_sync_operation["responses"]["202"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/TrialSyncResponse"}
    assert create_trial_sync_operation["responses"]["422"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/APIErrorResponse"}
    assert get_trial_sync_operation["operationId"] == "get_trial_sync"
    assert get_trial_sync_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/TrialSyncResponse"}
    assert get_trial_sync_operation["responses"]["404"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/APIErrorResponse"}
