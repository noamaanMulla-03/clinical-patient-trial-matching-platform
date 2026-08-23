"""Small, validated client for the ClinicalTrials.gov API v2 study endpoints."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_CLINICAL_TRIALS_API_BASE_URL = "https://clinicaltrials.gov/api/v2"
MAX_STUDIES_PAGE_SIZE = 1_000
_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class ClinicalTrialsClientError(ValueError):
    """Raised when ClinicalTrials.gov cannot provide a valid study payload."""


class ClinicalTrialsRequestError(ClinicalTrialsClientError):
    """Raised for a network or HTTP failure without exposing remote response content."""


class ClinicalTrialsResponseError(ClinicalTrialsClientError):
    """Raised when a successful response does not match the v2 JSON contract."""


@dataclass(frozen=True, slots=True)
class ClinicalTrialsStudiesPage:
    """One opaque-cursor page of unmodified ClinicalTrials.gov study records."""

    studies: tuple[dict[str, Any], ...]
    next_page_token: str | None
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class ClinicalTrialsStudyResponse:
    """One unmodified study record and the instant its HTTP response arrived."""

    study: dict[str, Any]
    retrieved_at: datetime


class ClinicalTrialsGovClient:
    """Fetch public study records without transforming or logging their content."""

    def __init__(
        self,
        base_url: str = DEFAULT_CLINICAL_TRIALS_API_BASE_URL,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._validate_base_url(base_url)
        # Keep the API's versioned path when composing relative endpoint URLs.
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            follow_redirects=False,
            timeout=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ClinicalTrialsGovClient:
        """Create a client from the configured versioned API base URL."""
        values = os.environ if environ is None else environ
        return cls(
            values.get(
                "CLINICAL_TRIALS_API_BASE_URL", DEFAULT_CLINICAL_TRIALS_API_BASE_URL
            ),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> ClinicalTrialsGovClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying connection pool when a job or request completes."""
        await self._client.aclose()

    async def get_study(self, nct_id: str) -> ClinicalTrialsStudyResponse:
        """Fetch one unmodified v2 study record by its NCT identifier."""
        if not _NCT_ID_PATTERN.fullmatch(nct_id):
            raise ClinicalTrialsClientError(
                "ClinicalTrials.gov study requests require an NCT identifier."
            )
        payload, retrieved_at = await self._get_json(f"studies/{nct_id}")
        return ClinicalTrialsStudyResponse(
            study=dict(self._require_mapping(payload, endpoint="study")),
            retrieved_at=retrieved_at,
        )

    async def search_studies(
        self,
        *,
        query_term: str | None = None,
        condition: str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> ClinicalTrialsStudiesPage:
        """Fetch one JSON page while callers retain the opaque next-request cursor."""
        if not 1 <= page_size <= MAX_STUDIES_PAGE_SIZE:
            raise ClinicalTrialsClientError(
                "ClinicalTrials.gov page_size must be between 1 and "
                f"{MAX_STUDIES_PAGE_SIZE}."
            )
        params: dict[str, str | int] = {"format": "json", "pageSize": page_size}
        if query_term is not None:
            params["query.term"] = self._require_nonblank(query_term, "query_term")
        if condition is not None:
            params["query.cond"] = self._require_nonblank(condition, "condition")
        if page_token is not None:
            if not page_token.strip():
                raise ClinicalTrialsClientError(
                    "ClinicalTrials.gov page_token must not be blank."
                )
            # The server defines this cursor; never rewrite an opaque token.
            params["pageToken"] = page_token

        response_payload, retrieved_at = await self._get_json("studies", params=params)
        payload = self._require_mapping(response_payload, endpoint="study search")
        studies = payload.get("studies")
        if not isinstance(studies, list) or not all(
            isinstance(study, Mapping) for study in studies
        ):
            raise ClinicalTrialsResponseError(
                "ClinicalTrials.gov study search response must contain a studies array."
            )
        next_page_token = payload.get("nextPageToken")
        if next_page_token is not None and not isinstance(next_page_token, str):
            raise ClinicalTrialsResponseError(
                "ClinicalTrials.gov nextPageToken must be a string when present."
            )
        return ClinicalTrialsStudiesPage(
            studies=tuple(dict(study) for study in studies),
            next_page_token=next_page_token,
            retrieved_at=retrieved_at,
        )

    async def _get_json(
        self, path: str, *, params: Mapping[str, str | int] | None = None
    ) -> tuple[Any, datetime]:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as error:
            raise ClinicalTrialsRequestError(
                "ClinicalTrials.gov request failed."
            ) from error
        if response.is_error:
            raise ClinicalTrialsRequestError(
                f"ClinicalTrials.gov returned HTTP {response.status_code}."
            )
        # Capture receipt before parsing so this remains the API response time, not
        # the later worker/database persistence time.
        retrieved_at = datetime.now(UTC)
        try:
            return response.json(), retrieved_at
        except ValueError as error:
            raise ClinicalTrialsResponseError(
                "ClinicalTrials.gov response was not valid JSON."
            ) from error

    @staticmethod
    def _validate_base_url(base_url: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ClinicalTrialsClientError(
                "ClinicalTrials.gov API base URL must be an absolute HTTPS URL."
            )

    @staticmethod
    def _require_nonblank(value: str, field_name: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ClinicalTrialsClientError(
                f"ClinicalTrials.gov {field_name} must not be blank."
            )
        return normalized_value

    @staticmethod
    def _require_mapping(payload: Any, *, endpoint: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ClinicalTrialsResponseError(
                f"ClinicalTrials.gov {endpoint} response must be a JSON object."
            )
        return payload
