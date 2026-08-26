"""Safe, consistent API errors and request-correlation helpers."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
OPENAPI_REQUEST_ID_RESPONSE_HEADER = {
    "description": (
        "Correlation ID for this request. Return it when reporting an API issue."
    ),
    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
}


class APIValidationIssue(BaseModel):
    """A field-level error that intentionally omits submitted values."""

    code: str
    message: str
    location: list[str | int]


class APIErrorDetail(BaseModel):
    """The stable machine-readable and safe human-readable error payload."""

    code: str
    message: str
    issues: list[APIValidationIssue] = Field(default_factory=list)


class APIErrorResponse(BaseModel):
    """The response body used for every handled API error."""

    error: APIErrorDetail
    request_id: str


class APIError(Exception):
    """An intentional API failure whose message is safe to return to a caller."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        issues: Sequence[APIValidationIssue] = (),
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.issues = list(issues)


def request_id_for(request: Request) -> str:
    """Return the request's safe correlation ID, generating one as a fallback."""
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    return str(uuid4())


def validation_issues_from_errors(
    errors: Sequence[Mapping[str, Any]],
) -> list[APIValidationIssue]:
    """Convert validation metadata without echoing FHIR or other submitted content."""
    return [
        APIValidationIssue(
            code=str(error.get("type", "invalid_request")),
            message="Invalid request field.",
            location=[
                part if isinstance(part, str | int) else str(part)
                for part in error.get("loc", ())
            ],
        )
        for error in errors
    ]


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach one validated request ID to every response and error body."""
    requested_id = request.headers.get(REQUEST_ID_HEADER)
    request.state.request_id = (
        requested_id
        if requested_id and _REQUEST_ID_PATTERN.fullmatch(requested_id)
        else str(uuid4())
    )
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id_for(request)
    return response


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    issues: Sequence[APIValidationIssue] = (),
) -> JSONResponse:
    """Build an error response without exposing exception details or request data."""
    request_id = request_id_for(request)
    payload = APIErrorResponse(
        error=APIErrorDetail(code=code, message=message, issues=list(issues)),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={REQUEST_ID_HEADER: request_id},
    )


def _http_error_code(status_code: int) -> str:
    """Map HTTP status codes to stable client-facing error codes."""
    return {
        status.HTTP_400_BAD_REQUEST: "bad_request",
        status.HTTP_401_UNAUTHORIZED: "unauthorized",
        status.HTTP_403_FORBIDDEN: "forbidden",
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    }.get(status_code, "request_failed")


def http_exception_response(
    request: Request, error: StarletteHTTPException
) -> JSONResponse:
    """Return a safe standard response for framework and route HTTP errors."""
    return _error_response(
        request,
        status_code=error.status_code,
        code=_http_error_code(error.status_code),
        message="The request could not be completed.",
    )


def install_api_error_handlers(app: FastAPI) -> None:
    """Install the shared error convention on the FastAPI application."""

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, error: APIError) -> JSONResponse:
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            issues=error.issues,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="Request validation failed.",
            issues=validation_issues_from_errors(error.errors()),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        return http_exception_response(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def handle_framework_http_error(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        return http_exception_response(request, error)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
        return _error_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="An unexpected error occurred.",
        )
