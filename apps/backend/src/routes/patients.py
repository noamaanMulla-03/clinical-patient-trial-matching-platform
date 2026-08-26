"""Patient routes for synthetic FHIR imports."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_database_session
from src.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from src.fhir.importer import FHIRPatientNormalizationError
from src.fhir.schemas import (
    FHIRImportRequest,
    FHIRImportResponse,
    PatientFactSourceResponse,
    PatientTimelineResponse,
)
from src.services.source_snapshots import (
    persist_synthetic_patient_import,
    retrieve_patient_fact_source,
    retrieve_patient_timeline,
)

router = APIRouter(tags=["patients"])
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


@router.post(
    "/patients/import/fhir",
    operation_id="import_synthetic_fhir_bundle",
    response_model=FHIRImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a synthetic FHIR R4 Patient Bundle",
    responses={
        201: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        422: {
            "description": "Invalid or non-synthetic FHIR Bundle.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def import_synthetic_fhir_bundle(
    import_request: FHIRImportRequest,
    session: DatabaseSession,
) -> FHIRImportResponse:
    """Persist a complete, source-linked import in one database transaction."""
    try:
        async with session.begin():
            result = await persist_synthetic_patient_import(session, import_request)
    except FHIRPatientNormalizationError as error:
        # The normalizer's messages are static and never contain submitted FHIR data.
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="fhir_import.invalid_patient_bundle",
            message=str(error),
        ) from error

    return FHIRImportResponse(
        patient_id=result.patient_id,
        patient_import_id=result.patient_import_id,
        fact_ids=list(result.fact_ids),
        data_quality_issues=list(result.data_quality_issues),
    )


@router.get(
    "/patients/{patient_id}",
    operation_id="get_synthetic_patient_timeline",
    response_model=PatientTimelineResponse,
    summary="Retrieve the latest completed synthetic patient timeline",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": "Synthetic patient was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def get_synthetic_patient_timeline(
    patient_id: str,
    session: DatabaseSession,
) -> PatientTimelineResponse:
    """Expose source-linked facts from one completed import without merging imports."""
    timeline = await retrieve_patient_timeline(session, patient_id)
    if timeline is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="patient.not_found",
            message="Synthetic patient was not found.",
        )
    return timeline


@router.get(
    "/patients/{patient_id}/facts/{fact_id}/source",
    operation_id="get_synthetic_patient_fact_source",
    response_model=PatientFactSourceResponse,
    summary="Retrieve immutable FHIR source evidence for a current timeline fact",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": (
                "Synthetic patient fact was not found in the current import."
            ),
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def get_synthetic_patient_fact_source(
    patient_id: str,
    fact_id: str,
    session: DatabaseSession,
) -> PatientFactSourceResponse:
    """Expose one source snapshot without merging it across patient imports."""
    source = await retrieve_patient_fact_source(session, patient_id, fact_id)
    if source is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="patient_fact.not_found",
            message="Synthetic patient fact was not found in the current import.",
        )
    return source
