"""Safe API models for bounded ClinicalTrials.gov sync jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import TrialSync
from app.workers.trial_ingestion import TrialIngestionJobError, TrialIngestionRequest


class TrialSyncCreateRequest(BaseModel):
    """A finite public-trial selection that can safely be handed to a worker."""

    model_config = ConfigDict(extra="forbid")

    nct_id: str | None = None
    query_term: str | None = None
    condition: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    page_size: int = Field(default=100, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_selection(self) -> TrialSyncCreateRequest:
        """Reuse worker validation so the API cannot queue an unsafe selection."""
        try:
            self.to_ingestion_request()
        except TrialIngestionJobError as error:
            # The worker errors are static and never include selector content.
            raise ValueError(str(error)) from error
        return self

    def to_ingestion_request(self) -> TrialIngestionRequest:
        """Build the normalized selection that is persisted in the job record."""
        return TrialIngestionRequest(
            nct_id=self.nct_id,
            query_term=self.query_term,
            condition=self.condition,
            start_page=self.start_page,
            end_page=self.end_page,
            page_size=self.page_size,
        )


class TrialSyncSelectionResponse(BaseModel):
    """The normalized bounded selector retained for a trial sync."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["nct_id", "search", "page_range"]
    collection_id: str | None = None
    nct_id: str | None = None
    query_term: str | None = None
    condition: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    page_size: int


class TrialSyncCountsResponse(BaseModel):
    """Safe aggregate ingestion counts, with no trial or patient content."""

    pages_fetched: int
    studies_processed: int
    versions_created: int
    unchanged_studies: int
    versions_requiring_reparse: int
    versions_reusing_matching_results: int


class TrialSyncSourceLagResponse(BaseModel):
    """Freshness metrics that distinguish missing and invalid source update dates."""

    records_with_update_time: int
    records_missing_update_time: int
    records_invalid_update_time: int
    max_lag_seconds: int | None = None


class TrialSyncFailureResponse(BaseModel):
    """A static failure summary that never includes remote response details."""

    code: str
    message: str


class TrialSyncResponse(BaseModel):
    """Durable operational state for a single queued or completed trial sync."""

    id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    selection: TrialSyncSelectionResponse
    counts: TrialSyncCountsResponse
    source_lag: TrialSyncSourceLagResponse
    failure: TrialSyncFailureResponse | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_record(cls, sync: TrialSync) -> TrialSyncResponse:
        """Translate an ORM record without exposing stored raw trial snapshots."""
        failure = (
            TrialSyncFailureResponse(
                code=sync.failure_code,
                message=sync.failure_message,
            )
            if sync.failure_code is not None and sync.failure_message is not None
            else None
        )
        return cls(
            id=sync.id,
            status=cast(
                Literal["queued", "running", "completed", "failed"], sync.status
            ),
            selection=TrialSyncSelectionResponse.model_validate(
                sync.request_parameters
            ),
            counts=TrialSyncCountsResponse(
                pages_fetched=sync.pages_fetched,
                studies_processed=sync.studies_processed,
                versions_created=sync.versions_created,
                unchanged_studies=sync.unchanged_studies,
                versions_requiring_reparse=sync.versions_requiring_reparse,
                versions_reusing_matching_results=sync.versions_reusing_matching_results,
            ),
            source_lag=TrialSyncSourceLagResponse(
                records_with_update_time=sync.source_records_with_update_time,
                records_missing_update_time=sync.source_records_missing_update_time,
                records_invalid_update_time=sync.source_records_invalid_update_time,
                max_lag_seconds=sync.max_source_lag_seconds,
            ),
            failure=failure,
            created_at=sync.created_at,
            started_at=sync.started_at,
            completed_at=sync.completed_at,
        )


class TrialCatalogueFreshnessResponse(BaseModel):
    """Aggregate freshness for the current public-trial projection only."""

    model_config = ConfigDict(extra="forbid")

    records_with_source_update_time: int
    records_missing_source_update_time: int
    oldest_source_update_at: datetime | None = None
    newest_source_update_at: datetime | None = None
    latest_retrieved_at: datetime | None = None


class TrialCatalogueStatusResponse(BaseModel):
    """Safe readiness state for the local public trial catalogue."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["empty", "ready", "updating"]
    searchable_trial_count: int
    latest_successful_update_at: datetime | None = None
    latest_sync: TrialSyncResponse | None = None
    freshness: TrialCatalogueFreshnessResponse


class TrialCatalogueTrialResponse(BaseModel):
    """Safe current projection fields for one public trial catalogue entry."""

    model_config = ConfigDict(extra="forbid")

    nct_id: str
    title: str | None = None
    study_status: str | None = None
    source_updated_at: datetime | None = None
    retrieved_at: datetime


class TrialCatalogueTrialsResponse(BaseModel):
    """A bounded, public-only view of the current trial catalogue."""

    model_config = ConfigDict(extra="forbid")

    total_count: int
    items: list[TrialCatalogueTrialResponse]
