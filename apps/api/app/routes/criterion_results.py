"""Reviewer-facing criterion detail and append-only correction routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.criteria.api_schemas import (
    CriterionDetailResponse,
    ReviewCorrectionRequest,
    ReviewCorrectionResponse,
)
from app.db.session import get_database_session
from app.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from app.services.criterion_details import (
    CriterionDetailError,
    CriterionDetailNotFoundError,
    append_reviewer_correction,
    retrieve_criterion_detail,
)

router = APIRouter(tags=["criterion-results"])
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


@router.get(
    "/criterion-results/{criterion_result_id}",
    operation_id="get_criterion_result_detail",
    response_model=CriterionDetailResponse,
    summary="Retrieve source-linked criterion evidence and audit history",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": "Criterion result was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def get_criterion_result_detail(
    criterion_result_id: UUID, session: DatabaseSession
) -> CriterionDetailResponse:
    """Return immutable result history without computing a new clinical outcome."""
    try:
        return await retrieve_criterion_detail(session, criterion_result_id)
    except CriterionDetailNotFoundError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="criterion_result.not_found",
            message=str(error),
        ) from error
    except CriterionDetailError as error:
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="criterion_result.inconsistent_snapshot",
            message="Criterion result evidence could not be safely displayed.",
        ) from error


@router.post(
    "/criterion-results/{criterion_result_id}/corrections",
    operation_id="append_criterion_reviewer_correction",
    response_model=ReviewCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append a reviewer correction to one criterion result",
    responses={
        201: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": "Criterion result was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
        409: {
            "description": "Reviewer correction cannot be appended.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def append_criterion_reviewer_correction_route(
    criterion_result_id: UUID,
    correction: ReviewCorrectionRequest,
    session: DatabaseSession,
) -> ReviewCorrectionResponse:
    """Persist a correction as a new immutable history item, never an overwrite."""
    try:
        async with session.begin():
            return await append_reviewer_correction(
                session,
                criterion_result_id=criterion_result_id,
                correction=correction,
            )
    except CriterionDetailNotFoundError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="criterion_result.not_found",
            message=str(error),
        ) from error
    except CriterionDetailError as error:
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="criterion_result.invalid_correction",
            message=str(error),
        ) from error
