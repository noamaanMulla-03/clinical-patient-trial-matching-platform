"""Unit tests for API error responses and request correlation."""

import asyncio
import json
from uuid import UUID

from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.errors import (
    http_exception_response,
    request_id_middleware,
    validation_issues_from_errors,
)
from app.main import app


def _request(*, request_id: str | None = None) -> Request:
    headers = [] if request_id is None else [(b"x-request-id", request_id.encode())]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


async def _successful_response(_: Request) -> Response:
    return Response(status_code=204)


def test_successful_responses_receive_a_generated_request_id() -> None:
    response = asyncio.run(request_id_middleware(_request(), _successful_response))

    assert response.status_code == 204
    UUID(response.headers["X-Request-ID"])


def test_http_errors_use_the_shared_envelope_and_preserve_safe_request_ids() -> None:
    request_id = "test-request-123"
    request = _request(request_id=request_id)
    asyncio.run(request_id_middleware(request, _successful_response))
    response = http_exception_response(request, HTTPException(status_code=404))

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert json.loads(response.body) == {
        "error": {
            "code": "not_found",
            "message": "The request could not be completed.",
            "issues": [],
        },
        "request_id": request_id,
    }


def test_framework_http_errors_use_the_shared_handler() -> None:
    assert StarletteHTTPException in app.exception_handlers


def test_validation_issues_never_echo_submitted_values() -> None:
    issues = validation_issues_from_errors(
        [
            {
                "type": "fhir_import.invalid_bundle",
                "loc": ("body", "bundle"),
                "input": {"resourceType": "Bundle", "entry": [{"name": "Test"}]},
            }
        ]
    )

    assert [issue.model_dump() for issue in issues] == [
        {
            "code": "fhir_import.invalid_bundle",
            "message": "Invalid request field.",
            "location": ["body", "bundle"],
        }
    ]
