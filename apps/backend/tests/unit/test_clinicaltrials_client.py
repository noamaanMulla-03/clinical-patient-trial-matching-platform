"""Contract tests for the ClinicalTrials.gov API v2 client without live requests."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.clients.clinicaltrials import (
    ClinicalTrialsClientError,
    ClinicalTrialsGovClient,
    ClinicalTrialsRequestError,
    ClinicalTrialsResponseError,
)

MockHandler = Callable[[httpx.Request], httpx.Response]
FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "fixtures" / "clinicaltrials-v2"
)


def _run(coroutine: Awaitable[Any]) -> Any:
    return asyncio.run(coroutine)


def _client(handler: MockHandler) -> ClinicalTrialsGovClient:
    return ClinicalTrialsGovClient(
        transport=httpx.MockTransport(handler),
    )


def _response_fixture(name: str) -> dict[str, Any]:
    """Load representative v2 payloads without calling the public API in tests."""
    payload = json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Fixture {name} must contain a JSON object.")
    return payload


def test_client_reads_the_configured_versioned_api_base_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/studies/NCT01234567"
        return httpx.Response(200, json={})

    async def check() -> None:
        async with ClinicalTrialsGovClient.from_environment(
            {"CLINICAL_TRIALS_API_BASE_URL": "https://example.test/api/v2"},
            transport=httpx.MockTransport(handler),
        ) as client:
            response = await client.get_study("NCT01234567")
        assert response.retrieved_at.tzinfo == UTC

    _run(check())


def test_get_study_uses_the_v2_study_endpoint_and_returns_the_full_record() -> None:
    study = _response_fixture("study-NCT01234567.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/studies/NCT01234567"
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=study)

    async def check() -> None:
        async with _client(handler) as client:
            response = await client.get_study("NCT01234567")
        assert response.study == study
        assert response.retrieved_at.tzinfo == UTC

    _run(check())


def test_search_studies_uses_v2_parameters_and_preserves_the_opaque_cursor() -> None:
    first_page = _response_fixture("studies-page-1.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/studies"
        assert dict(request.url.params) == {
            "format": "json",
            "pageSize": "25",
            "query.term": "lung cancer",
            "query.cond": "neoplasm",
            "pageToken": "opaque-cursor",
        }
        return httpx.Response(200, json=first_page)

    async def check() -> None:
        async with _client(handler) as client:
            page = await client.search_studies(
                query_term=" lung cancer ",
                condition="neoplasm",
                page_size=25,
                page_token="opaque-cursor",
            )
        assert page.studies == tuple(first_page["studies"])
        assert page.next_page_token == "opaque-page-token-2"
        assert page.retrieved_at.tzinfo == UTC

    _run(check())


def test_search_studies_accepts_a_final_fixture_page_without_a_cursor() -> None:
    final_page = _response_fixture("studies-page-final.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/studies"
        assert dict(request.url.params) == {
            "format": "json",
            "pageSize": "10",
            "pageToken": "opaque-page-token-2",
        }
        return httpx.Response(200, json=final_page)

    async def check() -> None:
        async with _client(handler) as client:
            page = await client.search_studies(
                page_size=10,
                page_token="opaque-page-token-2",
            )
        assert page.studies == tuple(final_page["studies"])
        assert page.next_page_token is None

    _run(check())


@pytest.mark.parametrize("nct_id", ["", "NCT123", "NCT012345678", "ABC01234567"])
def test_get_study_rejects_invalid_nct_identifiers(nct_id: str) -> None:
    async def check() -> None:
        async with _client(
            lambda _: pytest.fail("request should not be made")
        ) as client:
            with pytest.raises(ClinicalTrialsClientError):
                await client.get_study(nct_id)

    _run(check())


def test_search_rejects_invalid_payload_and_safe_http_errors() -> None:
    invalid_search_response = _response_fixture("studies-invalid.json")

    async def invalid_payload() -> None:
        async with _client(
            lambda _: httpx.Response(200, json=invalid_search_response)
        ) as client:
            with pytest.raises(ClinicalTrialsResponseError):
                await client.search_studies()

    async def http_error() -> None:
        async with _client(
            lambda _: httpx.Response(429, text="remote detail")
        ) as client:
            with pytest.raises(ClinicalTrialsRequestError, match="HTTP 429"):
                await client.search_studies()

    _run(invalid_payload())
    _run(http_error())
