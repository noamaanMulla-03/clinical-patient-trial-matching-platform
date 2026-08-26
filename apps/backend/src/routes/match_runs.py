"""Routes that queue and expose durable lexical and semantic match-run state."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MatchRun
from src.db.session import get_database_session
from src.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIError, APIErrorResponse
from src.matching.schemas import (
    MatchRunCreateRequest,
    MatchRunResponse,
    TrialMatchResponse,
)
from src.services.match_runs import (
    MatchRunCancellationError,
    MatchRunError,
    MatchRunNotFoundError,
    cancel_match_run,
    create_queued_match_run,
    match_run_response,
    match_run_results,
)

router = APIRouter(tags=["match-runs"])
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


@router.post(
    "/match-runs",
    operation_id="create_match_run",
    response_model=MatchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue lexical and semantic retrieval for one synthetic patient import",
    responses={
        202: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        422: {
            "description": "Patient import cannot be used for a match run.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def create_match_run(
    request: MatchRunCreateRequest, session: DatabaseSession
) -> MatchRunResponse:
    """Persist a queued job only; workers perform clinical retrieval asynchronously."""
    try:
        async with session.begin():
            run = await create_queued_match_run(
                session, patient_import_id=request.patient_import_id
            )
        return await match_run_response(session, run)
    except MatchRunError as error:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="match_run.invalid_patient_import",
            message=str(error),
        ) from error


@router.post(
    "/match-runs/{run_id}/cancel",
    operation_id="cancel_match_run",
    response_model=MatchRunResponse,
    summary="Cancel queued or active retrieval work",
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        404: {
            "description": "Match run was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
        409: {
            "description": "Match run is already terminal.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
)
async def cancel_queued_or_running_match_run(
    run_id: UUID, session: DatabaseSession
) -> MatchRunResponse:
    """Cancel durable work without removing its immutable input or result history."""
    try:
        async with session.begin():
            run = await cancel_match_run(session, run_id)
        return await match_run_response(session, run)
    except MatchRunNotFoundError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="match_run.not_found",
            message=str(error),
        ) from error
    except MatchRunCancellationError as error:
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="match_run.not_cancellable",
            message=str(error),
        ) from error


@router.get(
    "/match-runs/{run_id}",
    operation_id="get_match_run",
    response_model=MatchRunResponse,
    summary="Retrieve safe lexical and semantic match-run status",
    responses={
        404: {
            "description": "Match run was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        }
    },
)
async def get_match_run(run_id: UUID, session: DatabaseSession) -> MatchRunResponse:
    """Return durable status without returning patient-derived retrieval text."""
    run = await _match_run_or_404(session, run_id)
    return await match_run_response(session, run)


@router.get(
    "/match-runs/{run_id}/results",
    operation_id="get_match_run_results",
    response_model=list[TrialMatchResponse],
    summary="Retrieve persisted ranked lexical and semantic trial candidates",
    responses={
        404: {
            "description": "Match run was not found.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        }
    },
)
async def get_match_run_results(
    run_id: UUID, session: DatabaseSession
) -> list[TrialMatchResponse]:
    """Return only trial metadata, rank, score components, and bounded outcome."""
    run = await _match_run_or_404(session, run_id)
    return await match_run_results(session, run)


async def _match_run_or_404(session: AsyncSession, run_id: UUID) -> MatchRun:
    run = await session.get(MatchRun, run_id)
    if run is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="match_run.not_found",
            message="Match run was not found.",
        )
    return run
