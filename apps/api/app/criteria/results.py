"""Persistence boundary for deterministic criterion evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.criteria.evaluation import evaluate_atomic_criterion
from app.criteria.manual import ManualCriterionError, atomic_criterion_from_record
from app.criteria.schemas import CriterionEvaluation
from app.db.models import (
    Criterion,
    CriterionResult,
    MatchRun,
    PatientFactRecord,
    TrialMatch,
)
from app.fhir.schemas import PatientFact

DETERMINISTIC_EVALUATOR_VERSION = "deterministic-v1"


class CriterionResultError(ValueError):
    """Raised when a criterion result cannot be safely tied to its match inputs."""


async def evaluate_and_store_criterion_result(
    session: AsyncSession,
    *,
    trial_match_id: UUID,
    criterion_id: UUID,
    as_of: date,
    evaluator_version: str = DETERMINISTIC_EVALUATOR_VERSION,
) -> CriterionResult:
    """Evaluate only the immutable patient import selected by the match run."""
    trial_match, criterion, match_run = await _match_context(
        session, trial_match_id=trial_match_id, criterion_id=criterion_id
    )
    records = (
        await session.scalars(
            select(PatientFactRecord).where(
                PatientFactRecord.patient_import_id == match_run.patient_import_id
            )
        )
    ).all()
    try:
        facts = tuple(_patient_fact_from_record(record) for record in records)
        atomic_criterion = atomic_criterion_from_record(criterion)
    except (ManualCriterionError, ValidationError) as error:
        raise CriterionResultError(
            "Stored match inputs contain invalid normalized criterion or fact data."
        ) from error
    evaluation = evaluate_atomic_criterion(atomic_criterion, facts, as_of=as_of)
    return await store_criterion_result(
        session,
        trial_match=trial_match,
        criterion=criterion,
        match_run=match_run,
        evaluation=evaluation,
        evaluator_version=evaluator_version,
    )


async def store_criterion_result(
    session: AsyncSession,
    *,
    trial_match: TrialMatch,
    criterion: Criterion,
    match_run: MatchRun,
    evaluation: CriterionEvaluation,
    evaluator_version: str = DETERMINISTIC_EVALUATOR_VERSION,
) -> CriterionResult:
    """Store one source-grounded result after verifying every cited fact id."""
    normalized_version = evaluator_version.strip()
    if not normalized_version:
        raise CriterionResultError("Criterion results require an evaluator version.")
    if criterion.trial_version_id != trial_match.trial_version_id:
        raise CriterionResultError(
            "Criterion does not belong to this trial match version."
        )
    if len(set(evaluation.evidence_fact_ids)) != len(evaluation.evidence_fact_ids):
        raise CriterionResultError(
            "Criterion result evidence IDs must not be repeated."
        )
    if evaluation.outcome != "unknown" and not evaluation.evidence_fact_ids:
        raise CriterionResultError(
            "Non-unknown criterion results require evidence IDs."
        )
    await _require_existing_evidence_ids(
        session,
        evidence_fact_ids=evaluation.evidence_fact_ids,
        patient_import_id=match_run.patient_import_id,
    )
    existing = await session.scalar(
        select(CriterionResult.id).where(
            CriterionResult.trial_match_id == trial_match.id,
            CriterionResult.criterion_id == criterion.id,
        )
    )
    if existing is not None:
        raise CriterionResultError("A result already exists for this match criterion.")

    result = CriterionResult(
        id=uuid4(),
        trial_match_id=trial_match.id,
        criterion_id=criterion.id,
        outcome=evaluation.outcome,
        evidence_fact_ids=evaluation.evidence_fact_ids,
        evaluator_version=normalized_version,
        evaluation_path="deterministic",
        # The reason is a controlled code, never source clinical text.
        explanation=evaluation.reason,
        requires_review=evaluation.requires_review,
        evaluated_at=datetime.now(UTC),
    )
    session.add(result)
    await session.flush()
    return result


async def _match_context(
    session: AsyncSession, *, trial_match_id: UUID, criterion_id: UUID
) -> tuple[TrialMatch, Criterion, MatchRun]:
    trial_match = await session.get(TrialMatch, trial_match_id)
    criterion = await session.get(Criterion, criterion_id)
    if trial_match is None or criterion is None:
        raise CriterionResultError("Trial match or criterion was not found.")
    match_run = await session.get(MatchRun, trial_match.match_run_id)
    if match_run is None:
        raise CriterionResultError("Trial match has no match-run input snapshot.")
    if criterion.trial_version_id != trial_match.trial_version_id:
        raise CriterionResultError(
            "Criterion does not belong to this trial match version."
        )
    return trial_match, criterion, match_run


async def _require_existing_evidence_ids(
    session: AsyncSession,
    *,
    evidence_fact_ids: list[str],
    patient_import_id: UUID,
) -> None:
    if not evidence_fact_ids:
        return
    stored_ids = set(
        (
            await session.scalars(
                select(PatientFactRecord.id).where(
                    PatientFactRecord.patient_import_id == patient_import_id,
                    PatientFactRecord.id.in_(
                        evaluation_id for evaluation_id in evidence_fact_ids
                    ),
                )
            )
        ).all()
    )
    if stored_ids != set(evidence_fact_ids):
        # Facts from a different import are also rejected: historical evidence must
        # remain tied to the exact patient snapshot used for this match run.
        raise CriterionResultError(
            "Criterion result cites evidence missing from this patient import."
        )


def _patient_fact_from_record(record: PatientFactRecord) -> PatientFact:
    """Rebuild the shared normalized-fact contract from its immutable database row."""
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
