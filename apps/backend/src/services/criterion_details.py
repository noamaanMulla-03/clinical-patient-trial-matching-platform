"""Read source-linked criterion detail and append reviewer corrections safely."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.criteria.api_schemas import (
    CriterionAuditEventResponse,
    CriterionDetailResponse,
    CriterionEvaluationResponse,
    CriterionSourceResponse,
    ReviewCorrectionRequest,
    ReviewCorrectionResponse,
)
from src.criteria.schemas import CriterionCategory, CriterionOutcome
from src.db.models import (
    Criterion,
    CriterionResult,
    MatchRun,
    PatientFactRecord,
    PatientImport,
    ReviewDecision,
    TrialMatch,
)
from src.fhir.schemas import (
    ClinicalCode,
    DataQualityIssue,
    FactNormalization,
    FHIRProvenance,
    PatientFactKind,
    PatientFactResponse,
)


class CriterionDetailError(ValueError):
    """Raised when criterion review data cannot be safely retrieved or changed."""


class CriterionDetailNotFoundError(CriterionDetailError):
    """Raised when a requested result or its required snapshot context is absent."""


async def retrieve_criterion_detail(
    session: AsyncSession, criterion_result_id: UUID
) -> CriterionDetailResponse:
    """Read one result with evidence from its exact immutable match input snapshot."""
    result, criterion, match, run, patient_import = await _detail_context(
        session, criterion_result_id
    )
    decisions = list(
        await session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.criterion_result_id == result.id)
            .order_by(ReviewDecision.created_at, ReviewDecision.id)
        )
    )
    evidence = await _evidence_for_result(session, result, patient_import.id)
    current_outcome = _current_outcome(result.outcome, decisions)
    audit_history = [
        CriterionAuditEventResponse(
            id=result.id,
            event_type="deterministic_evaluation",
            occurred_at=result.evaluated_at,
            actor_id=f"evaluator:{result.evaluator_version}",
            outcome=cast(CriterionOutcome, result.outcome),
            reason=result.explanation,
            evaluation_path=result.evaluation_path,
        ),
        *[
            CriterionAuditEventResponse(
                id=decision.id,
                event_type="review_correction",
                occurred_at=decision.created_at,
                actor_id=decision.reviewer_id,
                outcome=cast(CriterionOutcome, decision.corrected_outcome),
                previous_outcome=cast(CriterionOutcome, decision.previous_outcome),
                reason=decision.reason,
            )
            for decision in decisions
        ],
    ]
    return CriterionDetailResponse(
        patient_id=patient_import.patient_id,
        trial_match_id=match.id,
        criterion=CriterionSourceResponse(
            id=criterion.id,
            category=cast(CriterionCategory, criterion.category),
            source_text=criterion.source_text,
            source_start=criterion.source_start,
            source_end=criterion.source_end,
            parsed_data=criterion.parsed_data,
            parser_version=criterion.parser_version,
            parser_confidence=criterion.parser_confidence,
            requires_human_review=criterion.requires_human_review,
            created_at=criterion.created_at,
        ),
        evaluation=CriterionEvaluationResponse(
            id=result.id,
            outcome=cast(CriterionOutcome, result.outcome),
            current_outcome=current_outcome,
            evidence_fact_ids=result.evidence_fact_ids,
            evaluator_version=result.evaluator_version,
            evaluation_path=result.evaluation_path,
            explanation=result.explanation,
            requires_review=result.requires_review,
            evaluated_at=result.evaluated_at,
        ),
        patient_evidence=evidence,
        audit_history=audit_history,
    )


async def append_reviewer_correction(
    session: AsyncSession,
    *,
    criterion_result_id: UUID,
    correction: ReviewCorrectionRequest,
) -> ReviewCorrectionResponse:
    """Append a reviewer correction without overwriting deterministic provenance."""
    result = await session.get(CriterionResult, criterion_result_id)
    if result is None:
        raise CriterionDetailNotFoundError("Criterion result was not found.")
    decisions = list(
        await session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.criterion_result_id == result.id)
            .order_by(ReviewDecision.created_at, ReviewDecision.id)
        )
    )
    previous_outcome = _current_outcome(result.outcome, decisions)
    if correction.corrected_outcome == previous_outcome:
        raise CriterionDetailError(
            "Reviewer correction must select a different criterion outcome."
        )
    decision = ReviewDecision(
        id=uuid4(),
        criterion_result_id=result.id,
        reviewer_id=correction.reviewer_id,
        previous_outcome=previous_outcome,
        corrected_outcome=correction.corrected_outcome,
        reason=correction.reason,
    )
    session.add(decision)
    await session.flush()
    return ReviewCorrectionResponse(
        id=decision.id,
        criterion_result_id=decision.criterion_result_id,
        reviewer_id=decision.reviewer_id,
        previous_outcome=cast(CriterionOutcome, decision.previous_outcome),
        corrected_outcome=cast(CriterionOutcome, decision.corrected_outcome),
        reason=decision.reason,
        created_at=decision.created_at,
    )


async def _detail_context(
    session: AsyncSession, criterion_result_id: UUID
) -> tuple[CriterionResult, Criterion, TrialMatch, MatchRun, PatientImport]:
    result = await session.get(CriterionResult, criterion_result_id)
    if result is None:
        raise CriterionDetailNotFoundError("Criterion result was not found.")
    criterion = await session.get(Criterion, result.criterion_id)
    match = await session.get(TrialMatch, result.trial_match_id)
    if criterion is None or match is None:
        raise CriterionDetailNotFoundError("Criterion result context was not found.")
    if criterion.trial_version_id != match.trial_version_id:
        raise CriterionDetailError(
            "Criterion result has inconsistent trial source data."
        )
    run = await session.get(MatchRun, match.match_run_id)
    if run is None:
        raise CriterionDetailNotFoundError(
            "Criterion result match input was not found."
        )
    patient_import = await session.get(PatientImport, run.patient_import_id)
    if patient_import is None or patient_import.status != "completed":
        raise CriterionDetailNotFoundError(
            "Criterion result patient input was not found."
        )
    return result, criterion, match, run, patient_import


async def _evidence_for_result(
    session: AsyncSession, result: CriterionResult, patient_import_id: UUID
) -> list[PatientFactResponse]:
    if not result.evidence_fact_ids:
        return []
    records = list(
        await session.scalars(
            select(PatientFactRecord).where(
                PatientFactRecord.patient_import_id == patient_import_id,
                PatientFactRecord.id.in_(result.evidence_fact_ids),
            )
        )
    )
    by_id = {record.id: record for record in records}
    if set(by_id) != set(result.evidence_fact_ids):
        raise CriterionDetailError(
            "Criterion result evidence is inconsistent with its patient import."
        )
    return [
        _patient_fact_response(by_id[fact_id]) for fact_id in result.evidence_fact_ids
    ]


def _patient_fact_response(record: PatientFactRecord) -> PatientFactResponse:
    """Keep displayed evidence formatted exactly as the patient timeline boundary."""
    return PatientFactResponse(
        fact_id=record.id,
        kind=cast(PatientFactKind, record.kind),
        code=ClinicalCode.model_validate(record.code),
        value=record.value,
        unit=record.unit,
        effective_at=record.effective_at,
        source=FHIRProvenance.model_validate(record.provenance),
        source_resource=record.source_resource,
        normalization=FactNormalization.model_validate(record.normalization),
        quality_issues=[
            DataQualityIssue.model_validate(issue) for issue in record.quality_issues
        ],
    )


def _current_outcome(
    initial_outcome: str, decisions: list[ReviewDecision]
) -> CriterionOutcome:
    outcome = decisions[-1].corrected_outcome if decisions else initial_outcome
    return cast(CriterionOutcome, outcome)
