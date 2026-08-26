"""Operational routes that do not process clinical content."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from src.errors import OPENAPI_REQUEST_ID_RESPONSE_HEADER, APIErrorResponse

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Response confirming that the API process can serve requests."""

    status: Literal["ok"]


@router.get(
    "/health",
    operation_id="health_check",
    response_model=HealthResponse,
    responses={
        200: {"headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER}},
        500: {
            "description": "Unexpected server error.",
            "model": APIErrorResponse,
            "headers": {"X-Request-ID": OPENAPI_REQUEST_ID_RESPONSE_HEADER},
        },
    },
    summary="Check API process availability",
    description=(
        "Confirms that the HTTP API process is available. "
        "It does not check database, Redis, or worker readiness."
    ),
)
async def health_check() -> HealthResponse:
    """Return a dependency-free health response for local diagnostics."""
    return HealthResponse(status="ok")
