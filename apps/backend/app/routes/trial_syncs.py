"""Routes for creating and observing bounded ClinicalTrials.gov sync jobs."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Trial, TrialSync
from app.db.session import get_database_session
from app.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from app.trials.development_collection import queue_development_trial_collection
from app.trials.schemas import (
    TrialCatalogueFreshnessResponse,
    TrialCatalogueStatusResponse,
    TrialCatalogueTrialResponse,
    TrialCatalogueTrialsResponse,
    TrialSyncCreateRequest,
    TrialSyncResponse,
)
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


@router.post(
    "/trial-syncs/development-collection",
    operation_id="queue_development_trial_collection",
    response_model=list[TrialSyncResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue the fixed local development trial collection",
    responses={
        202: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def queue_fixed_development_trial_collection(
    session: DatabaseSession,
) -> list[TrialSyncResponse]:
    """Queue only the source-controlled demo IDs, never a client-supplied list."""
    async with session.begin():
        syncs = await queue_development_trial_collection(session)
    return [TrialSyncResponse.from_record(sync) for sync in syncs]


@router.get(
    "/trial-catalogue",
    operation_id="get_trial_catalogue_status",
    response_model=TrialCatalogueStatusResponse,
    summary="Retrieve safe readiness and freshness for current public trial records",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def get_trial_catalogue_status(
    session: DatabaseSession,
) -> TrialCatalogueStatusResponse:
    """Expose aggregate catalogue state without returning source snapshots or logs."""
    freshness_row = (
        await session.execute(
            select(
                func.count(Trial.nct_id),
                func.count(Trial.source_updated_at),
                func.min(Trial.source_updated_at),
                func.max(Trial.source_updated_at),
                func.max(Trial.retrieved_at),
            )
        )
    ).one()
    total_trials = int(freshness_row[0])
    records_with_update_time = int(freshness_row[1])
    latest_sync = await session.scalar(
        select(TrialSync)
        .order_by(TrialSync.created_at.desc(), TrialSync.id.desc())
        .limit(1)
    )
    latest_successful_update_at = await session.scalar(
        select(TrialSync.completed_at)
        .where(TrialSync.status == "completed")
        .order_by(TrialSync.completed_at.desc(), TrialSync.id.desc())
        .limit(1)
    )
    sync_in_progress = (
        await session.scalar(
            select(TrialSync.id)
            .where(TrialSync.status.in_(("queued", "running")))
            .limit(1)
        )
    ) is not None
    state: Literal["empty", "ready", "updating"] = (
        "updating" if sync_in_progress else "ready" if total_trials else "empty"
    )
    return TrialCatalogueStatusResponse(
        state=state,
        searchable_trial_count=total_trials,
        latest_successful_update_at=latest_successful_update_at,
        latest_sync=(
            TrialSyncResponse.from_record(latest_sync)
            if latest_sync is not None
            else None
        ),
        freshness=TrialCatalogueFreshnessResponse(
            records_with_source_update_time=records_with_update_time,
            records_missing_source_update_time=(
                total_trials - records_with_update_time
            ),
            oldest_source_update_at=freshness_row[2],
            newest_source_update_at=freshness_row[3],
            latest_retrieved_at=freshness_row[4],
        ),
    )


@router.get(
    "/trial-catalogue/trials",
    operation_id="get_current_trial_catalogue",
    response_model=TrialCatalogueTrialsResponse,
    summary="List a bounded public-only view of current trial catalogue entries",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def get_current_trial_catalogue(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> TrialCatalogueTrialsResponse:
    """List current public projections without exposing raw snapshots or worker logs."""
    total_count = int(
        await session.scalar(select(func.count(Trial.nct_id)).select_from(Trial)) or 0
    )
    trials = list(
        await session.scalars(select(Trial).order_by(Trial.nct_id).limit(limit))
    )
    return TrialCatalogueTrialsResponse(
        total_count=total_count,
        items=[
            TrialCatalogueTrialResponse(
                nct_id=trial.nct_id,
                title=trial.title,
                study_status=trial.status,
                source_updated_at=trial.source_updated_at,
                retrieved_at=trial.retrieved_at,
            )
            for trial in trials
        ],
    )


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
