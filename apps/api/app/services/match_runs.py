"""Create and safely read durable lexical match-run records."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Criterion,
    CriterionResult,
    MatchRun,
    MatchRunCancellation,
    PatientImport,
    ReviewDecision,
    TrialMatch,
    TrialVersion,
)
from app.matching.schemas import (
    CriterionResultSummary,
    MatchRunResponse,
    TrialMatchResponse,
)
from app.trials.extraction import TrialExtractionError, extract_trial_fields

MAX_MATCH_RUN_CANDIDATES = 100
LEXICAL_RETRIEVAL_VERSION = "lexical-v1"
_MATCH_RUN_VERSIONS = {
    "parser": "manual-v1",
    "retrieval": LEXICAL_RETRIEVAL_VERSION,
    "rule_engine": "deterministic-v1",
    "terminology_mapping": "source-coded-v1",
    "prompt": "not-used-v1",
    "model_configuration": "not-used-v1",
}


def match_run_candidate_limit(run: MatchRun) -> int:
    """Read the immutable run-specific cap instead of a mutable global setting."""
    candidate_limit = run.configuration_snapshot["candidate_limit"]
    if type(candidate_limit) is not int or candidate_limit < 1:
        raise MatchRunError("Match run has an invalid candidate limit.")
    return candidate_limit


class MatchRunError(ValueError):
    """Raised when a match run cannot be created or safely accessed."""


class MatchRunNotFoundError(MatchRunError):
    """Raised when a requested durable match run does not exist."""


class MatchRunCancellationError(MatchRunError):
    """Raised when a terminal match run cannot transition to cancelled."""


async def create_queued_match_run(
    session: AsyncSession, *, patient_import_id: UUID
) -> MatchRun:
    """Queue one run only for a completed immutable patient import snapshot."""
    patient_import = await session.get(PatientImport, patient_import_id)
    if patient_import is None or patient_import.status != "completed":
        raise MatchRunError("Match runs require a completed patient import.")
    run = MatchRun(
        patient_import_id=patient_import.id,
        configuration_snapshot=_configuration_snapshot(patient_import.id),
        parser_version=_MATCH_RUN_VERSIONS["parser"],
        retrieval_version=_MATCH_RUN_VERSIONS["retrieval"],
        rule_engine_version=_MATCH_RUN_VERSIONS["rule_engine"],
        terminology_mapping_version=_MATCH_RUN_VERSIONS["terminology_mapping"],
        prompt_version=_MATCH_RUN_VERSIONS["prompt"],
        model_configuration_version=_MATCH_RUN_VERSIONS["model_configuration"],
        status="queued",
    )
    session.add(run)
    await session.flush()
    return run


async def cancel_match_run(session: AsyncSession, run_id: UUID) -> MatchRun:
    """Record cancellation without locking a worker's mutable operational row."""
    run = await session.get(MatchRun, run_id)
    if run is None:
        raise MatchRunNotFoundError("Match run was not found.")
    if run.status not in {"queued", "running"}:
        raise MatchRunCancellationError(
            "Only queued or running match runs can be cancelled."
        )
    if await session.get(MatchRunCancellation, run_id) is None:
        session.add(MatchRunCancellation(match_run_id=run_id))
    await session.flush()
    return run


async def match_run_response(session: AsyncSession, run: MatchRun) -> MatchRunResponse:
    """Return safe status and candidate count without exposing patient-derived text."""
    candidate_count = await session.scalar(
        select(func.count())
        .select_from(TrialMatch)
        .where(TrialMatch.match_run_id == run.id)
    )
    cancellation_requested = await session.get(MatchRunCancellation, run.id)
    return MatchRunResponse.from_record(
        run,
        candidate_count=int(candidate_count or 0),
        cancellation_requested=cancellation_requested is not None,
    )


async def match_run_results(
    session: AsyncSession, run: MatchRun
) -> list[TrialMatchResponse]:
    """Read persisted ranked candidates and their exact immutable trial versions."""
    matches = list(
        await session.scalars(
            select(TrialMatch)
            .where(TrialMatch.match_run_id == run.id)
            .order_by(TrialMatch.candidate_rank)
        )
    )
    versions = {
        version.id: version
        for version in await session.scalars(
            select(TrialVersion).where(
                TrialVersion.id.in_(match.trial_version_id for match in matches)
            )
        )
    }
    patient_import = await session.get(PatientImport, run.patient_import_id)
    if patient_import is None:
        raise MatchRunError("Match run patient import was not found.")
    criterion_summaries = await _criterion_summaries_by_match(session, matches)
    results: list[TrialMatchResponse] = []
    for match in matches:
        version = versions.get(match.trial_version_id)
        if version is None:
            continue
        title, study_status = _trial_display_metadata(version)
        results.append(
            TrialMatchResponse.from_record(
                match,
                patient_id=patient_import.patient_id,
                nct_id=version.nct_id,
                title=title,
                study_status=study_status,
                source_updated_at=version.source_updated_at,
                criterion_results=criterion_summaries.get(match.id, []),
            )
        )
    return results


async def _criterion_summaries_by_match(
    session: AsyncSession, matches: list[TrialMatch]
) -> dict[UUID, list[CriterionResultSummary]]:
    """Attach review links without recomputing or mutating criterion outcomes."""
    match_ids = [match.id for match in matches]
    if not match_ids:
        return {}
    rows = (
        await session.execute(
            select(CriterionResult, Criterion)
            .join(Criterion, Criterion.id == CriterionResult.criterion_id)
            .where(CriterionResult.trial_match_id.in_(match_ids))
            .order_by(CriterionResult.evaluated_at, CriterionResult.id)
        )
    ).all()
    result_ids = [result.id for result, _ in rows]
    decisions_by_result: dict[UUID, list[ReviewDecision]] = {}
    if result_ids:
        for decision in await session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.criterion_result_id.in_(result_ids))
            .order_by(ReviewDecision.created_at, ReviewDecision.id)
        ):
            decisions_by_result.setdefault(decision.criterion_result_id, []).append(
                decision
            )

    summaries: dict[UUID, list[CriterionResultSummary]] = {}
    for result, criterion in rows:
        decisions = decisions_by_result.get(result.id, [])
        current_outcome = (
            decisions[-1].corrected_outcome if decisions else result.outcome
        )
        summaries.setdefault(result.trial_match_id, []).append(
            CriterionResultSummary(
                id=result.id,
                category=criterion.category,
                source_text=criterion.source_text,
                outcome=result.outcome,
                current_outcome=cast(
                    Literal["met", "not_met", "unknown", "conflicting"],
                    current_outcome,
                ),
                requires_review=result.requires_review,
            )
        )
    return summaries


def _trial_display_metadata(version: TrialVersion) -> tuple[str | None, str | None]:
    """Read display metadata from the exact trial snapshot used for matching."""
    try:
        fields = extract_trial_fields(version.raw_study)
    except TrialExtractionError:
        # Old or corrupt source snapshots must not be represented as current metadata.
        return None, None
    return fields.title, fields.status


def _configuration_snapshot(patient_import_id: UUID) -> dict[str, object]:
    """Freeze candidate policy, input identity, and engine versions before queueing."""
    return {
        "patient_import_id": str(patient_import_id),
        "candidate_limit": MAX_MATCH_RUN_CANDIDATES,
        "candidate_generation": LEXICAL_RETRIEVAL_VERSION,
        "metadata_filtering": "conservative-v1",
        "scoring": "field-weighted-lexical-v1",
        "versions": _MATCH_RUN_VERSIONS,
    }
