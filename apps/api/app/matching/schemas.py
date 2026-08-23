"""Safe API contracts for asynchronous lexical match runs and their candidates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import MatchRun, TrialMatch


class MatchRunCreateRequest(BaseModel):
    """Select one completed synthetic patient import for lexical candidate retrieval."""

    model_config = ConfigDict(extra="forbid")

    patient_import_id: UUID


class MatchRunFailureResponse(BaseModel):
    """Static terminal failure details that never contain clinical input text."""

    code: str
    message: str


class MatchRunResponse(BaseModel):
    """Durable operational status without exposing normalized clinical input text."""

    id: UUID
    patient_import_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    candidate_count: int = Field(ge=0)
    cancellation_requested: bool
    configuration_versions: dict[str, str]
    created_at: datetime
    candidate_limit: int = Field(gt=0)
    started_at: datetime | None = None
    failure: MatchRunFailureResponse | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_record(
        cls, run: MatchRun, *, candidate_count: int, cancellation_requested: bool
    ) -> MatchRunResponse:
        candidate_limit = run.configuration_snapshot["candidate_limit"]
        if type(candidate_limit) is not int or candidate_limit < 1:
            raise ValueError("Match run has an invalid candidate limit.")
        failure = (
            MatchRunFailureResponse(code=run.failure_code, message=run.failure_message)
            if run.failure_code is not None and run.failure_message is not None
            else None
        )
        return cls(
            patient_import_id=run.patient_import_id,
            id=run.id,
            status=cast(
                Literal["queued", "running", "completed", "failed", "cancelled"],
                run.status,
            ),
            candidate_count=candidate_count,
            cancellation_requested=cancellation_requested,
            candidate_limit=candidate_limit,
            configuration_versions={
                "parser": run.parser_version,
                "retrieval": run.retrieval_version,
                "rule_engine": run.rule_engine_version,
                "terminology_mapping": run.terminology_mapping_version,
                "prompt": run.prompt_version,
                "model_configuration": run.model_configuration_version,
            },
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            failure=failure,
        )


class TrialMatchResponse(BaseModel):
    """A ranked retrieval candidate; this is not an eligibility or enrollment result."""

    id: UUID
    trial_version_id: UUID
    nct_id: str
    title: str | None = None
    candidate_rank: int = Field(gt=0)
    retrieval_scores: dict[str, Any]
    outcome: (
        Literal["potential_match", "likely_excluded", "needs_review", "not_relevant"]
        | None
    ) = None
    created_at: datetime
    evaluated_at: datetime | None = None

    @classmethod
    def from_record(
        cls, match: TrialMatch, *, nct_id: str, title: str | None
    ) -> TrialMatchResponse:
        return cls(
            id=match.id,
            trial_version_id=match.trial_version_id,
            nct_id=nct_id,
            title=title,
            candidate_rank=match.candidate_rank,
            retrieval_scores=match.retrieval_scores,
            outcome=cast(
                Literal[
                    "potential_match",
                    "likely_excluded",
                    "needs_review",
                    "not_relevant",
                ]
                | None,
                match.outcome,
            ),
            created_at=match.created_at,
            evaluated_at=match.evaluated_at,
        )
