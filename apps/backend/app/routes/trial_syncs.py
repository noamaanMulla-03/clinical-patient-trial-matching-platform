"""Routes for creating and observing bounded ClinicalTrials.gov sync jobs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrialSync
from app.db.session import get_database_session
from app.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from app.trials.schemas import TrialSyncCreateRequest, TrialSyncResponse
from app.workers.trial_ingestion import create_queued_trial_sync

router = APIRouter(tags=["trial-syncs"])
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


@router.post(
    "/trial-syncs",
    operation_id="create_trial_sync",
    response_model=TrialSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a bounded ClinicalTrials.gov trial sync",
    responses={
        202: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        422: {
            "description": "Invalid or unbounded trial sync selection.",
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
async def create_trial_sync(
    request: TrialSyncCreateRequest,
    session: DatabaseSession,
) -> TrialSyncResponse:
    """Persist a validated job for a worker without making a remote API call inline."""
    async with session.begin():
        sync = await create_queued_trial_sync(session, request.to_ingestion_request())
    return TrialSyncResponse.from_record(sync)


@router.get(
    "/trial-syncs/{job_id}",
    operation_id="get_trial_sync",
    response_model=TrialSyncResponse,
    summary="Retrieve safe status for a ClinicalTrials.gov trial sync",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": "Trial sync job was not found.",
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
async def get_trial_sync(job_id: UUID, session: DatabaseSession) -> TrialSyncResponse:
    """Return status and aggregate metrics without returning source trial payloads."""
    sync = await session.get(TrialSync, job_id)
    if sync is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="trial_sync.not_found",
            message="Trial sync job was not found.",
        )
    return TrialSyncResponse.from_record(sync)
