"""Background hybrid match-run job with immutable ranked trial snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.criteria.aggregation import aggregate_trial_match
from src.criteria.results import (
    CriterionResultError,
    evaluate_and_store_criterion_result,
)
from src.db.models import (
    Criterion,
    MatchRun,
    MatchRunCancellation,
    PatientFactRecord,
    TrialMatch,
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
from src.retrieval.semantic import (
    SemanticCoverage,
    SemanticRetrievalIncompleteError,
    SemanticTrialCandidate,
    semantic_coverage,
    semantic_trial_candidates,
)
from src.retrieval.trial_documents import document_from_trial_version
from src.services.match_runs import (
    match_run_candidate_limit,
    match_run_catalogue_as_of,
    match_run_pool_limit,
)


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
    catalogue_as_of = match_run_catalogue_as_of(run)
    review_limit = match_run_candidate_limit(run)
    lexical_pool_limit = match_run_pool_limit(run, "lexical")
    semantic_pool_limit = match_run_pool_limit(run, "semantic")
    fusion_pool_limit = match_run_pool_limit(run, "fusion")
    if not query.terms:
        run.retrieval_execution = _retrieval_execution(
            query,
            facts,
            mode="insufficient_evidence",
            degradation_reasons=["no_usable_retrieval_facts"],
            catalogue_as_of=catalogue_as_of,
        )
        return
    lexical_versions = list(
        await session.scalars(
            lexical_trial_candidates_statement(
                query,
                candidate_limit=lexical_pool_limit,
                catalogue_as_of=catalogue_as_of,
            )
        )
    )
    lexical_trials = [
        document_from_trial_version(version) for version in lexical_versions
    ]
    scored_trials = [
        (trial, score)
        for trial in lexical_trials
        if trial_matches_metadata(metadata_from_trial(trial), query.filters)
        and (score := score_trial_candidate(trial, query)) is not None
    ]
    ranked_lexical_trials = rank_scored_trials(scored_trials)
    await _raise_if_cancelled(session, run)
    semantic_candidates, semantic_state = await _semantic_candidates_or_empty(
        session,
        query,
        candidate_limit=semantic_pool_limit,
        catalogue_as_of=catalogue_as_of,
    )
    filtered_semantic_candidates = tuple(
        candidate
        for candidate in semantic_candidates
        if trial_matches_metadata(metadata_from_trial(candidate.trial), query.filters)
    )
    fused_trials = fuse_ranked_trial_candidates(
        ranked_lexical_trials,
        filtered_semantic_candidates,
        candidate_limit=fusion_pool_limit,
    )
    ranked_trials = rerank_fused_trial_candidates(
        fused_trials,
        query,
        candidate_limit=review_limit,
    )
    await _raise_if_cancelled(session, run)
    rank = 0
    persisted_matches: list[TrialMatch] = []
    await _raise_if_cancelled(session, run)
    for trial, score in ranked_trials:
        rank += 1
        match = TrialMatch(
            match_run_id=run.id,
            trial_version_id=trial.trial_version_id,
            candidate_rank=rank,
            retrieval_scores=score,
        )
        session.add(match)
        persisted_matches.append(match)
    await session.flush()
    for match in persisted_matches:
        await _raise_if_cancelled(session, run)
        await _evaluate_retrieved_trial(session, match, run)
    run.retrieval_execution = _retrieval_execution(
        query,
        facts,
        mode=semantic_state["mode"],
        degradation_reasons=semantic_state["degradation_reasons"],
        lexical_pool_count=len(lexical_trials),
        lexical_filtered_count=len(ranked_lexical_trials),
        semantic_pool_count=len(semantic_candidates),
        semantic_filtered_count=len(filtered_semantic_candidates),
        fused_count=len(fused_trials),
        persisted_count=rank,
        coverage=semantic_state["coverage"],
        catalogue_as_of=catalogue_as_of,
    )
    await session.flush()


async def _evaluate_retrieved_trial(
    session: AsyncSession, match: TrialMatch, run: MatchRun
) -> None:
    """Assess every retained candidate against the exact immutable run input.

    Retrieval rank stays immutable. Missing, malformed, or review-required criteria
    are deliberately aggregated as needs_review rather than causing reassurance.
    """
    criteria = list(
        await session.scalars(
            select(Criterion)
            .where(Criterion.trial_version_id == match.trial_version_id)
            .order_by(Criterion.source_start, Criterion.id)
        )
    )
    for criterion in criteria:
        await _raise_if_cancelled(session, run)
        try:
            await evaluate_and_store_criterion_result(
                session,
                trial_match_id=match.id,
                criterion_id=criterion.id,
                as_of=run.created_at.date(),
            )
        except CriterionResultError:
            # Aggregation below sees incomplete results and produces needs_review.
            # Do not persist source text or exception detail in operational status.
            continue
    await aggregate_trial_match(session, trial_match_id=match.id)


async def _semantic_candidates_or_empty(
    session: AsyncSession,
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
    catalogue_as_of: datetime,
) -> tuple[tuple[SemanticTrialCandidate, ...], dict[str, object]]:
    """Return candidates plus an explicit, non-clinical degradation record."""
    coverage = await semantic_coverage(session, catalogue_as_of=catalogue_as_of)
    try:
        candidates = await semantic_trial_candidates(
            session,
            query,
            candidate_limit=candidate_limit,
            catalogue_as_of=catalogue_as_of,
        )
    except SemanticRetrievalIncompleteError:
        return (), {
            "mode": "lexical_only_partial_semantic_catalogue",
            "degradation_reasons": ["semantic_vector_coverage_incomplete"],
            "coverage": _coverage_payload(coverage),
        }
    except EmbeddingEncoderError:
        return (), {
            "mode": "lexical_only_model_unavailable",
            "degradation_reasons": ["semantic_model_unavailable"],
            "coverage": _coverage_payload(coverage),
        }
    return candidates, {
        "mode": "hybrid" if candidates else "lexical_only_no_semantic_candidates",
        "degradation_reasons": (
            [] if candidates else ["semantic_branch_returned_no_candidates"]
        ),
        "coverage": _coverage_payload(coverage),
    }


def _retrieval_execution(
    query: PatientDerivedRetrievalQuery,
    facts: tuple[PatientFact, ...],
    *,
    mode: str,
    degradation_reasons: list[str],
    lexical_pool_count: int = 0,
    lexical_filtered_count: int = 0,
    semantic_pool_count: int = 0,
    semantic_filtered_count: int = 0,
    fused_count: int = 0,
    persisted_count: int = 0,
    coverage: dict[str, int] | None = None,
    catalogue_as_of: datetime | None = None,
) -> dict[str, object]:
    """Persist replay-oriented metadata without raw patient query text or vectors."""
    included_fact_ids = sorted(term.source_fact_id for term in query.terms)
    included = set(included_fact_ids)
    manifest = {
        "included_fact_ids": included_fact_ids,
        "omitted_fact_ids": sorted(
            str(fact.fact_id) for fact in facts if str(fact.fact_id) not in included
        ),
        "omitted_fact_reasons": {
            str(fact.fact_id): _omission_reasons(fact)
            for fact in facts
            if str(fact.fact_id) not in included
        },
        "term_kinds": sorted(term.kind for term in query.terms),
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return {
        "mode": mode,
        "degradation_reasons": degradation_reasons,
        "query_manifest_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "query_manifest": manifest,
        "counts": {
            "lexical_pool": lexical_pool_count,
            "lexical_after_filters": lexical_filtered_count,
            "semantic_pool": semantic_pool_count,
            "semantic_after_filters": semantic_filtered_count,
            "fused": fused_count,
            "persisted": persisted_count,
        },
        "semantic_coverage": coverage or {"current_trials": 0, "embedded_trials": 0},
        "catalogue": {
            "as_of": catalogue_as_of.isoformat() if catalogue_as_of else None,
            "policy": "immutable-trial-version-as-of-v1",
        },
    }


def _coverage_payload(coverage: SemanticCoverage) -> dict[str, int]:
    return {
        "current_trials": coverage.current_trial_count,
        "embedded_trials": coverage.embedded_trial_count,
    }


def _omission_reasons(fact: PatientFact) -> list[str]:
    """Record safe query omissions without storing clinical values or text."""
    issue_codes = sorted({issue.code for issue in fact.quality_issues})
    return issue_codes or [f"unsupported_retrieval_fact_kind:{fact.kind}"]


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
