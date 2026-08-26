"""Bounded ClinicalTrials.gov ingestion job with immutable source snapshots."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients.clinicaltrials import (
    MAX_STUDIES_PAGE_SIZE,
    ClinicalTrialsClientError,
    ClinicalTrialsGovClient,
    ClinicalTrialsRequestError,
    ClinicalTrialsResponseError,
    ClinicalTrialsStudiesPage,
    ClinicalTrialsStudyResponse,
)
from src.db.models import Trial, TrialSync, TrialVersion
from src.services.source_snapshots import (
    TrialSnapshotError,
    canonical_json_snapshot,
    store_trial_version,
)
from src.trials.extraction import (
    SourceUpdateTime,
    TrialExtractionError,
    extract_source_update_time,
    extract_trial_fields,
)

MAX_TRIAL_INGESTION_PAGES = 10
DEFAULT_TRIAL_INGESTION_PAGE_SIZE = 100
_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class TrialIngestionJobError(ValueError):
    """Raised when an ingestion job would be unsafe or lacks a trial identity."""


class TrialStudiesClient(Protocol):
    """The small client surface needed by the ingestion job and its tests."""

    async def get_study(self, nct_id: str) -> ClinicalTrialsStudyResponse: ...

    async def search_studies(
        self,
        *,
        query_term: str | None = None,
        condition: str | None = None,
        page_size: int = DEFAULT_TRIAL_INGESTION_PAGE_SIZE,
        page_token: str | None = None,
    ) -> ClinicalTrialsStudiesPage: ...


@dataclass(frozen=True, slots=True)
class TrialIngestionRequest:
    """One finite source selection for a background trial-ingestion job.

    A page range without a query is allowed only when both boundaries are explicit.
    This makes a broad public-data crawl deliberate and limits it to ten pages.
    """

    nct_id: str | None = None
    collection_id: str | None = None
    query_term: str | None = None
    condition: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    page_size: int = DEFAULT_TRIAL_INGESTION_PAGE_SIZE

    def __post_init__(self) -> None:
        normalized_nct_id = _normalize_optional_text(self.nct_id, "nct_id")
        normalized_collection_id = _normalize_optional_text(
            self.collection_id, "collection_id"
        )
        normalized_query_term = _normalize_optional_text(self.query_term, "query_term")
        normalized_condition = _normalize_optional_text(self.condition, "condition")
        object.__setattr__(self, "nct_id", normalized_nct_id)
        object.__setattr__(self, "query_term", normalized_query_term)
        object.__setattr__(self, "condition", normalized_condition)
        object.__setattr__(self, "collection_id", normalized_collection_id)

        if (
            type(self.page_size) is not int
            or not 1 <= self.page_size <= MAX_STUDIES_PAGE_SIZE
        ):
            raise TrialIngestionJobError(
                "Trial ingestion page_size must be between 1 and "
                f"{MAX_STUDIES_PAGE_SIZE}."
            )

        has_page_boundary = self.start_page is not None or self.end_page is not None
        if normalized_nct_id is not None:
            if not _NCT_ID_PATTERN.fullmatch(normalized_nct_id):
                raise TrialIngestionJobError(
                    "Trial ingestion requires an NCT identifier in NCT######## format."
                )
            if normalized_query_term is not None or normalized_condition is not None:
                raise TrialIngestionJobError(
                    "An NCT ingestion cannot also include a query or condition."
                )
            if has_page_boundary:
                raise TrialIngestionJobError(
                    "An NCT ingestion cannot include a page range."
                )
            return

        if not has_page_boundary:
            if normalized_query_term is None and normalized_condition is None:
                raise TrialIngestionJobError(
                    "Trial ingestion requires an NCT identifier, query, condition, "
                    "or explicit page range."
                )
            object.__setattr__(self, "start_page", 1)
            object.__setattr__(self, "end_page", 1)

        if self.start_page is None or self.end_page is None:
            raise TrialIngestionJobError(
                "Trial ingestion page ranges require both start_page and end_page."
            )
        if type(self.start_page) is not int or type(self.end_page) is not int:
            raise TrialIngestionJobError(
                "Trial ingestion page boundaries must be whole numbers."
            )
        if self.start_page < 1 or self.end_page < self.start_page:
            raise TrialIngestionJobError(
                "Trial ingestion pages must start at 1 and end at or after the start."
            )
        if self.end_page > MAX_TRIAL_INGESTION_PAGES:
            raise TrialIngestionJobError(
                f"Trial ingestion cannot fetch beyond page {MAX_TRIAL_INGESTION_PAGES}."
            )

    @property
    def mode(self) -> Literal["nct_id", "search", "page_range"]:
        """Return the source-selection mode without exposing source content."""
        if self.nct_id is not None:
            return "nct_id"
        if self.query_term is not None or self.condition is not None:
            return "search"
        return "page_range"


@dataclass(frozen=True, slots=True)
class TrialIngestionResult:
    """Counts suitable for job status without retaining raw trial response content."""

    sync_id: UUID
    mode: Literal["nct_id", "search", "page_range"]
    status: Literal["completed", "failed"]
    pages_fetched: int
    studies_processed: int
    versions_created: int
    unchanged_studies: int
    versions_requiring_reparse: int
    versions_reusing_matching_results: int
    source_records_with_update_time: int
    source_records_missing_update_time: int
    source_records_invalid_update_time: int
    max_source_lag_seconds: int | None
    failure_code: str | None
    failure_message: str | None


@dataclass(slots=True)
class _IngestionMetrics:
    """Mutable counts kept only until the enclosing sync transaction completes."""

    pages_fetched: int = 0
    studies_processed: int = 0
    versions_created: int = 0
    unchanged_studies: int = 0
    versions_requiring_reparse: int = 0
    versions_reusing_matching_results: int = 0
    source_records_with_update_time: int = 0
    source_records_missing_update_time: int = 0
    source_records_invalid_update_time: int = 0
    max_source_lag_seconds: int | None = None

    def record_source_update(
        self, source_update: SourceUpdateTime, retrieved_at: datetime
    ) -> None:
        """Record lag only when the public posted date is safe to interpret."""
        if source_update.state == "missing":
            self.source_records_missing_update_time += 1
            return
        if source_update.state != "available" or source_update.value is None:
            self.source_records_invalid_update_time += 1
            return
        lag_seconds = int((retrieved_at - source_update.value).total_seconds())
        if lag_seconds < 0:
            self.source_records_invalid_update_time += 1
            return
        self.source_records_with_update_time += 1
        self.max_source_lag_seconds = max(
            lag_seconds,
            self.max_source_lag_seconds or 0,
        )


@dataclass(frozen=True, slots=True)
class _StoredStudy:
    """One source-storage result paired with public freshness data."""

    trial_version: TrialVersion | None
    source_update: SourceUpdateTime


async def run_trial_ingestion_job(
    session: AsyncSession,
    request: TrialIngestionRequest,
    *,
    client: TrialStudiesClient,
) -> TrialIngestionResult:
    """Run one sync with durable status and rollback-safe source persistence."""
    sync = await create_queued_trial_sync(session, request)
    return await _run_trial_sync(session, sync=sync, request=request, client=client)


async def run_queued_trial_ingestion_job(
    session: AsyncSession, sync_id: UUID
) -> TrialIngestionResult:
    """Claim and run one durable queued sync using its frozen request selection."""
    claimed_sync_id = await session.scalar(
        update(TrialSync)
        .where(TrialSync.id == sync_id, TrialSync.status == "queued")
        .values(status="running", started_at=datetime.now(UTC))
        .returning(TrialSync.id)
    )
    sync = await session.get(TrialSync, sync_id)
    if sync is None:
        raise TrialIngestionJobError("Trial sync job was not found.")
    if claimed_sync_id is None:
        raise TrialIngestionJobError("Trial sync job is not queued for processing.")

    try:
        request = _request_from_parameters(sync.request_parameters)
    except TrialIngestionJobError as error:
        sync.status = "failed"
        sync.failure_code, sync.failure_message = _safe_failure_details(error)
        sync.completed_at = datetime.now(UTC)
        await session.flush()
        return _result_from_sync(sync)

    async with ClinicalTrialsGovClient.from_environment() as client:
        return await _run_trial_sync(session, sync=sync, request=request, client=client)


async def _run_trial_sync(
    session: AsyncSession,
    *,
    sync: TrialSync,
    request: TrialIngestionRequest,
    client: TrialStudiesClient,
) -> TrialIngestionResult:
    """Run a claimed sync while preserving its durable terminal status on failure."""
    if sync.status == "queued":
        sync.status = "running"
        sync.started_at = datetime.now(UTC)
        await session.flush()

    try:
        # Preserve the failed job record while rolling back partial trial snapshots.
        async with session.begin_nested():
            metrics = await _ingest_studies(session, request, client)
    except Exception as error:
        sync.status = "failed"
        sync.failure_code, sync.failure_message = _safe_failure_details(error)
        sync.completed_at = datetime.now(UTC)
        await session.flush()
        return _result_from_sync(sync)

    _apply_metrics(sync, metrics)
    sync.status = "completed"
    sync.completed_at = datetime.now(UTC)
    await session.flush()
    return _result_from_sync(sync)


async def create_queued_trial_sync(
    session: AsyncSession, request: TrialIngestionRequest
) -> TrialSync:
    """Persist one validated, bounded selection before a worker claims it.

    The route and worker deliberately share this serializer so the durable job
    record always represents the exact normalized selection that was approved.
    """
    # Database ``now()`` is transaction-stable; capture wall-clock queue time so
    # status views can order several selections created in one request reliably.
    sync = TrialSync(
        request_parameters=_request_parameters(request), created_at=datetime.now(UTC)
    )
    session.add(sync)
    await session.flush()
    return sync


async def run_configured_trial_ingestion_job(
    session: AsyncSession, request: TrialIngestionRequest
) -> TrialIngestionResult:
    """Run the job using the environment-configured ClinicalTrials.gov client."""
    async with ClinicalTrialsGovClient.from_environment() as client:
        return await run_trial_ingestion_job(session, request, client=client)


def _request_from_parameters(parameters: Mapping[str, Any]) -> TrialIngestionRequest:
    """Revalidate the persisted selector before a worker can make a remote call."""
    try:
        return TrialIngestionRequest(
            nct_id=_optional_parameter(parameters, "nct_id"),
            collection_id=_optional_parameter(parameters, "collection_id"),
            query_term=_optional_parameter(parameters, "query_term"),
            condition=_optional_parameter(parameters, "condition"),
            start_page=_integer_parameter(parameters, "start_page"),
            end_page=_integer_parameter(parameters, "end_page"),
            page_size=_required_integer_parameter(parameters, "page_size"),
        )
    except (TypeError, TrialIngestionJobError) as error:
        raise TrialIngestionJobError(
            "Trial sync has an invalid stored selection."
        ) from error


def _optional_parameter(parameters: Mapping[str, Any], name: str) -> str | None:
    value = parameters.get(name)
    if value is None or isinstance(value, str):
        return value
    raise TrialIngestionJobError("Trial sync has an invalid stored selection.")


def _integer_parameter(parameters: Mapping[str, Any], name: str) -> int | None:
    value = parameters.get(name)
    if value is None or type(value) is int:
        return value
    raise TrialIngestionJobError("Trial sync has an invalid stored selection.")


def _required_integer_parameter(parameters: Mapping[str, Any], name: str) -> int:
    value = _integer_parameter(parameters, name)
    if value is None:
        raise TrialIngestionJobError("Trial sync has an invalid stored selection.")
    return value


async def _ingest_studies(
    session: AsyncSession,
    request: TrialIngestionRequest,
    client: TrialStudiesClient,
) -> _IngestionMetrics:
    """Fetch and store the finite selection inside the caller's savepoint."""
    metrics = _IngestionMetrics()
    if request.nct_id is not None:
        response = await client.get_study(request.nct_id)
        await _record_study(
            session,
            response.study,
            retrieved_at=response.retrieved_at,
            metrics=metrics,
        )
        return metrics

    if request.start_page is None or request.end_page is None:
        raise TrialIngestionJobError(
            "Trial ingestion search is missing its page range."
        )

    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    for page_number in range(1, request.end_page + 1):
        page = await client.search_studies(
            query_term=request.query_term,
            condition=request.condition,
            page_size=request.page_size,
            page_token=page_token,
        )
        metrics.pages_fetched += 1
        if page_number >= request.start_page:
            for study in page.studies:
                await _record_study(
                    session,
                    study,
                    retrieved_at=page.retrieved_at,
                    metrics=metrics,
                )
        if page_number == request.end_page or page.next_page_token is None:
            break
        if not page.next_page_token.strip() or page.next_page_token in seen_page_tokens:
            raise TrialIngestionJobError(
                "ClinicalTrials.gov returned an invalid repeated page cursor."
            )
        seen_page_tokens.add(page.next_page_token)
        page_token = page.next_page_token
    return metrics


async def _record_study(
    session: AsyncSession,
    study: Mapping[str, Any],
    *,
    retrieved_at: datetime,
    metrics: _IngestionMetrics,
) -> None:
    """Persist one study and update counts without retaining raw source in status."""
    metrics.studies_processed += 1
    stored_study = await _store_study_if_changed(
        session,
        study,
        retrieved_at=retrieved_at,
    )
    metrics.record_source_update(stored_study.source_update, retrieved_at)
    if stored_study.trial_version is None:
        metrics.unchanged_studies += 1
        return
    metrics.versions_created += 1
    if stored_study.trial_version.requires_reparse:
        metrics.versions_requiring_reparse += 1
    else:
        metrics.versions_reusing_matching_results += 1


async def _store_study_if_changed(
    session: AsyncSession,
    study: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> _StoredStudy:
    """Store one study only when its exact source snapshot is not already present."""
    extracted_fields = extract_trial_fields(study)
    source_update = extract_source_update_time(study)
    nct_id = extracted_fields.nct_id
    _, source_hash = canonical_json_snapshot(study)
    existing_version_id = await session.scalar(
        select(TrialVersion.id).where(
            TrialVersion.nct_id == nct_id,
            TrialVersion.source_hash == source_hash,
        )
    )
    if existing_version_id is not None:
        trial = await session.get(Trial, nct_id)
        if trial is None:
            raise TrialIngestionJobError(
                "ClinicalTrials.gov snapshot is missing its current trial projection."
            )
        # Keep current freshness accurate without duplicating an identical immutable
        # source snapshot or triggering unnecessary downstream parsing work.
        trial.retrieved_at = retrieved_at
        trial.source_updated_at = source_update.value
        await session.flush()
        return _StoredStudy(trial_version=None, source_update=source_update)

    return _StoredStudy(
        trial_version=await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=study,
            retrieved_at=retrieved_at,
            source_updated_at=source_update.value,
            extracted_fields=extracted_fields,
        ),
        source_update=source_update,
    )


def _request_parameters(request: TrialIngestionRequest) -> dict[str, Any]:
    """Serialize the bounded source selection without any returned trial content."""
    return {
        "mode": request.mode,
        "collection_id": request.collection_id,
        "nct_id": request.nct_id,
        "query_term": request.query_term,
        "condition": request.condition,
        "start_page": request.start_page,
        "end_page": request.end_page,
        "page_size": request.page_size,
    }


def _apply_metrics(sync: TrialSync, metrics: _IngestionMetrics) -> None:
    """Copy completed work counts into the durable sync record."""
    sync.pages_fetched = metrics.pages_fetched
    sync.studies_processed = metrics.studies_processed
    sync.versions_created = metrics.versions_created
    sync.unchanged_studies = metrics.unchanged_studies
    sync.versions_requiring_reparse = metrics.versions_requiring_reparse
    sync.versions_reusing_matching_results = metrics.versions_reusing_matching_results
    sync.source_records_with_update_time = metrics.source_records_with_update_time
    sync.source_records_missing_update_time = metrics.source_records_missing_update_time
    sync.source_records_invalid_update_time = metrics.source_records_invalid_update_time
    sync.max_source_lag_seconds = metrics.max_source_lag_seconds


def _result_from_sync(sync: TrialSync) -> TrialIngestionResult:
    """Return only operational state and safe counts from a durable sync record."""
    if sync.status not in {"completed", "failed"}:
        raise TrialIngestionJobError("Trial sync did not reach a terminal status.")
    return TrialIngestionResult(
        sync_id=sync.id,
        mode=cast(
            Literal["nct_id", "search", "page_range"], sync.request_parameters["mode"]
        ),
        status=cast(Literal["completed", "failed"], sync.status),
        pages_fetched=sync.pages_fetched,
        studies_processed=sync.studies_processed,
        versions_created=sync.versions_created,
        unchanged_studies=sync.unchanged_studies,
        versions_requiring_reparse=sync.versions_requiring_reparse,
        versions_reusing_matching_results=sync.versions_reusing_matching_results,
        source_records_with_update_time=sync.source_records_with_update_time,
        source_records_missing_update_time=sync.source_records_missing_update_time,
        source_records_invalid_update_time=sync.source_records_invalid_update_time,
        max_source_lag_seconds=sync.max_source_lag_seconds,
        failure_code=sync.failure_code,
        failure_message=sync.failure_message,
    )


def _safe_failure_details(error: Exception) -> tuple[str, str]:
    """Classify failures without putting source payloads in durable state."""
    if isinstance(error, ClinicalTrialsRequestError):
        return "remote_request_failed", "ClinicalTrials.gov request failed."
    if isinstance(error, ClinicalTrialsResponseError):
        return "remote_response_invalid", "ClinicalTrials.gov returned invalid data."
    if isinstance(error, ClinicalTrialsClientError):
        return "remote_request_invalid", "ClinicalTrials.gov request was invalid."
    if isinstance(error, TrialExtractionError):
        return "source_extract_invalid", "Trial source fields could not be extracted."
    if isinstance(error, TrialSnapshotError):
        return "snapshot_persist_invalid", "Trial source snapshot could not be stored."
    if isinstance(error, TrialIngestionJobError):
        return (
            "ingestion_request_invalid",
            "Trial ingestion request could not be completed.",
        )
    return "internal_error", "Trial ingestion failed unexpectedly."


def _normalize_optional_text(value: str | None, field_name: str) -> str | None:
    """Trim optional selectors while rejecting blank values before remote requests."""
    if value is None:
        return None
    if not isinstance(value, str) or not (normalized_value := value.strip()):
        raise TrialIngestionJobError(
            f"Trial ingestion {field_name} must be a non-blank string when provided."
        )
    return normalized_value
