"""Background hybrid match-run job with immutable ranked trial snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    MatchRun,
    MatchRunCancellation,
    PatientFactRecord,
    TrialMatch,
    TrialVersion,
)
from src.fhir.schemas import PatientFact
from src.retrieval.embedding_encoder import EmbeddingEncoderError
from src.retrieval.filtering import metadata_from_trial, trial_matches_metadata
from src.retrieval.fusion import fuse_ranked_trial_candidates
from src.retrieval.lexical import lexical_trial_candidates_statement
from src.retrieval.query_builder import build_patient_retrieval_query
from src.retrieval.reranking import rerank_fused_trial_candidates
from src.retrieval.schemas import PatientDerivedRetrievalQuery
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate
from src.retrieval.semantic import SemanticTrialCandidate, semantic_trial_candidates
from src.services.match_runs import match_run_candidate_limit


class MatchRunJobError(ValueError):
    """Raised when a durable match run is not ready for a worker claim."""


class MatchRunCancelledError(MatchRunJobError):
    """Raised internally when a worker reaches a cooperative cancellation boundary."""


async def run_match_run_job(session: AsyncSession, match_run_id: UUID) -> MatchRun:
    """Claim one queued run, retrieve candidates, and persist deterministic rankings."""
    claimed_run_id = await session.scalar(
        update(MatchRun)
        .where(MatchRun.id == match_run_id, MatchRun.status == "queued")
        .values(status="running", started_at=datetime.now(UTC))
        .returning(MatchRun.id)
    )
    run = await session.get(MatchRun, match_run_id)
    if run is None:
        raise MatchRunJobError("Match run was not found.")
    if claimed_run_id is None:
        if run.status == "cancelled":
            return run
        raise MatchRunJobError("Match run is not queued for processing.")
    try:
        async with session.begin_nested():
            await _persist_ranked_candidates(session, run)
    except MatchRunCancelledError:
        run.status = "cancelled"
        run.completed_at = datetime.now(UTC)
    except Exception as error:
        run.status = "failed"
        run.failure_code, run.failure_message = _safe_failure_details(error)
        run.completed_at = datetime.now(UTC)
    else:
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
    await session.flush()
    return run


async def _persist_ranked_candidates(session: AsyncSession, run: MatchRun) -> None:
    await _raise_if_cancelled(session, run)
    records = list(
        await session.scalars(
            select(PatientFactRecord).where(
                PatientFactRecord.patient_import_id == run.patient_import_id
            )
        )
    )
    try:
        facts = tuple(_patient_fact_from_record(record) for record in records)
    except ValidationError as error:
        raise MatchRunJobError(
            "Match run contains invalid normalized patient facts."
        ) from error
    query = build_patient_retrieval_query(facts, as_of=run.created_at.date())
    candidate_limit = match_run_candidate_limit(run)
    lexical_trials = list(
        await session.scalars(
            lexical_trial_candidates_statement(query, candidate_limit=candidate_limit)
        )
    )
    scored_trials = [
        (trial, score)
        for trial in lexical_trials
        if trial_matches_metadata(metadata_from_trial(trial), query.filters)
        and (score := score_trial_candidate(trial, query)) is not None
    ]
    ranked_lexical_trials = rank_scored_trials(scored_trials)
    await _raise_if_cancelled(session, run)
    semantic_candidates = await _semantic_candidates_or_empty(
        session, query, candidate_limit=candidate_limit
    )
    filtered_semantic_candidates = tuple(
        candidate
        for candidate in semantic_candidates
        if trial_matches_metadata(metadata_from_trial(candidate.trial), query.filters)
    )
    fused_trials = fuse_ranked_trial_candidates(
        ranked_lexical_trials,
        filtered_semantic_candidates,
        candidate_limit=candidate_limit,
    )
    ranked_trials = rerank_fused_trial_candidates(
        fused_trials,
        query,
        candidate_limit=candidate_limit,
    )
    await _raise_if_cancelled(session, run)
    versions = {
        version.nct_id: version
        for version in await session.scalars(
            select(TrialVersion).where(
                TrialVersion.nct_id.in_(trial.nct_id for trial, _ in ranked_trials),
                TrialVersion.superseded_at.is_(None),
            )
        )
    }
    rank = 0
    await _raise_if_cancelled(session, run)
    for trial, score in ranked_trials:
        version = versions.get(trial.nct_id)
        if version is None:
            continue
        rank += 1
        session.add(
            TrialMatch(
                match_run_id=run.id,
                trial_version_id=version.id,
                candidate_rank=rank,
                retrieval_scores=score,
            )
        )
    await session.flush()


async def _semantic_candidates_or_empty(
    session: AsyncSession,
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
) -> tuple[SemanticTrialCandidate, ...]:
    """Keep lexical retrieval available if the local semantic model is unavailable."""
    try:
        return await semantic_trial_candidates(
            session,
            query,
            candidate_limit=candidate_limit,
        )
    except EmbeddingEncoderError:
        # The public-trial vectors remain intact; a later run can retry locally.
        return ()


def _patient_fact_from_record(record: PatientFactRecord) -> PatientFact:
    """Rebuild the shared fact boundary without mixing patient import snapshots."""
    return PatientFact.model_validate(
        {
            "fact_id": record.id,
            "patient_id": record.patient_id,
            "kind": record.kind,
            "code": record.code,
            "value": record.value,
            "unit": record.unit,
            "effective_at": record.effective_at,
            "source": record.provenance,
            "source_resource": record.source_resource,
            "normalization": record.normalization,
            "quality_issues": record.quality_issues,
        }
    )


async def _raise_if_cancelled(session: AsyncSession, run: MatchRun) -> None:
    """Check the separate request record between bounded work stages."""
    cancellation = await session.get(MatchRunCancellation, run.id)
    if cancellation is not None:
        raise MatchRunCancelledError("Match run was cancelled.")


def _safe_failure_details(error: Exception) -> tuple[str, str]:
    """Map errors to static details so clinical content never reaches job status."""
    if isinstance(error, MatchRunJobError):
        return "match_run.invalid_input", "Match run inputs could not be processed."
    return "match_run.unexpected_error", "Match run could not be completed."
