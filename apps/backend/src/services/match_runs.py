"""Create and safely read durable hybrid retrieval match-run records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.db.models import (
    Criterion,
    CriterionResult,
    MatchRun,
    MatchRunCancellation,
    PatientImport,
    ReviewDecision,
    TrialMatch,
    TrialVersion,
)
from src.matching.schemas import (
    CriterionResultSummary,
    MatchRunResponse,
    TrialMatchResponse,
)
from src.retrieval.fusion import (
    RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
    RECIPROCAL_RANK_FUSION_VERSION,
)
from src.retrieval.reranking import STRUCTURED_EVIDENCE_RERANKER_VERSION
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
from src.trials.extraction import TrialExtractionError, extract_trial_fields

MAX_MATCH_RUN_CANDIDATES = 100
LEXICAL_POOL_LIMIT = 400
SEMANTIC_POOL_LIMIT = 400
FUSION_POOL_LIMIT = 250
HYBRID_RETRIEVAL_VERSION = "lexical-semantic-rrf-structured-reranker-v1"
_MATCH_RUN_VERSIONS = {
    "parser": ELIGIBILITY_PARSER_CONFIGURATION.parser_version,
    "retrieval": HYBRID_RETRIEVAL_VERSION,
    "rule_engine": "deterministic-v1",
    "terminology_mapping": "source-coded-v1",
    "prompt": "not-used-v1",
    "model_configuration": SEMANTIC_EMBEDDING_MODEL.configuration_version,
}


def match_run_candidate_limit(run: MatchRun) -> int:
    """Read the immutable run-specific cap instead of a mutable global setting."""
    candidate_limit = run.configuration_snapshot["candidate_limit"]
    if type(candidate_limit) is not int or candidate_limit < 1:
        raise MatchRunError("Match run has an invalid candidate limit.")
    return candidate_limit


def match_run_pool_limit(run: MatchRun, name: str) -> int:
    """Read a frozen stage-specific retrieval bound from one run contract."""
    pools = run.configuration_snapshot.get("candidate_pools")
    if not isinstance(pools, dict):
        raise MatchRunError("Match run has invalid candidate pools.")
    value = pools.get(name)
    if type(value) is not int or value < 1:
        raise MatchRunError("Match run has an invalid candidate pool limit.")
    return value


def match_run_catalogue_as_of(run: MatchRun) -> datetime:
    """Read the immutable catalogue cut-off recorded when the run was queued."""
    value = run.configuration_snapshot.get("catalogue_as_of")
    if not isinstance(value, str):
        raise MatchRunError("Match run has no catalogue snapshot time.")
    try:
        as_of = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MatchRunError(
            "Match run has an invalid catalogue snapshot time."
        ) from error
    if as_of.tzinfo is None:
        raise MatchRunError(
            "Match run catalogue snapshot time must include a timezone."
        )
    return as_of.astimezone(UTC)


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
    catalogue_as_of = datetime.now(UTC)
    run = MatchRun(
        patient_import_id=patient_import.id,
        configuration_snapshot=_configuration_snapshot(
            patient_import.id, catalogue_as_of=catalogue_as_of
        ),
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
        response = TrialMatchResponse.from_record(
            match,
            patient_id=patient_import.patient_id,
            nct_id=version.nct_id,
            title=title,
            study_status=study_status,
            source_updated_at=version.source_updated_at,
            criterion_results=criterion_summaries.get(match.id, []),
        )
        response.outcome = _current_assessment_outcome(
            criterion_summaries.get(match.id, [])
        )
        results.append(response)
    return results


def _current_assessment_outcome(
    results: list[CriterionResultSummary],
) -> Literal["potential_match", "likely_excluded", "needs_review", "not_relevant"]:
    """Derive the displayed assessment from immutable results and corrections.

    The stored retrieval event is never rewritten. A correction can only produce a
    new conservative display outcome, never make unsupported evidence reassuring.
    """
    if not results or any(
        result.requires_review or result.current_outcome in {"unknown", "conflicting"}
        for result in results
    ):
        return "needs_review"
    if any(
        result.category == "exclusion" and result.current_outcome == "not_met"
        for result in results
    ):
        return "likely_excluded"
    if any(
        result.category == "inclusion" and result.current_outcome == "not_met"
        for result in results
    ):
        return "not_relevant"
    return "potential_match"


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


def _configuration_snapshot(
    patient_import_id: UUID, *, catalogue_as_of: datetime
) -> dict[str, object]:
    """Freeze candidate policy, input identity, and engine versions before queueing."""
    return {
        "patient_import_id": str(patient_import_id),
        "catalogue_as_of": catalogue_as_of.isoformat(),
        "catalogue_policy": "immutable-trial-version-as-of-v1",
        "candidate_limit": MAX_MATCH_RUN_CANDIDATES,
        "candidate_pools": {
            "lexical": LEXICAL_POOL_LIMIT,
            "semantic": SEMANTIC_POOL_LIMIT,
            "fusion": FUSION_POOL_LIMIT,
            "review": MAX_MATCH_RUN_CANDIDATES,
        },
        "candidate_generation": HYBRID_RETRIEVAL_VERSION,
        "metadata_filtering": "conservative-v1",
        "scoring": "field-weighted-lexical-v1",
        "semantic_candidate_policy": "metadata-filtered-v1",
        "rank_fusion": {
            "method": RECIPROCAL_RANK_FUSION_VERSION,
            "rank_constant": RECIPROCAL_RANK_FUSION_RANK_CONSTANT,
        },
        "second_stage_reranker": {
            "method": STRUCTURED_EVIDENCE_RERANKER_VERSION,
            "policy": "direct-support-promotes-unknown-neutral-v1",
            "documented_conflicts": "filtered-before-reranking-v1",
        },
        "embedding_model": SEMANTIC_EMBEDDING_MODEL.snapshot(),
        "criterion_parser": ELIGIBILITY_PARSER_CONFIGURATION.snapshot(),
        "versions": _MATCH_RUN_VERSIONS,
    }
