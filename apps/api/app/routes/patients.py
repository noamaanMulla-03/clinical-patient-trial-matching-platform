"""Patient routes for synthetic FHIR imports."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_database_session
from app.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from app.fhir.importer import FHIRPatientNormalizationError
from app.fhir.schemas import FHIRImportRequest, FHIRImportResponse
from app.services.source_snapshots import persist_synthetic_patient_import

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
    )
