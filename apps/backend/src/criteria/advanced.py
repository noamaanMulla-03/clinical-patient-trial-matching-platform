"""Deterministic evaluation for units, dates, and documented care events."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal

from src.criteria.schemas import (
    AtomicCriterion,
    CriterionEvaluation,
    CriterionEvaluationReason,
    DateWindowRule,
    MedicationStatusRule,
    ProcedureStatusRule,
    RecencyWindowRule,
)
from src.fhir.schemas import MedicationFactValue, PatientFact, ProcedureFactValue


def evaluate_date_window(
    criterion: AtomicCriterion,
    rule: DateWindowRule,
    facts: Iterable[PatientFact],
) -> CriterionEvaluation:
    """Evaluate a source-normalized event date against an inclusive date window."""
    candidates = _facts_for_exact_code(
        facts, kind=rule.fact_kind, system=rule.code.system, code=rule.code.value
    )
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    if not candidates:
        return _unknown("missing_evidence")
    if any(fact.effective_at is None for fact in candidates):
        return _unknown("missing_date", candidates)
    predicate_values = {
        (rule.starts_on is None or fact.effective_at.date() >= rule.starts_on)
        and (rule.ends_on is None or fact.effective_at.date() <= rule.ends_on)
        for fact in candidates
        if fact.effective_at is not None
    }
    if len(predicate_values) != 1:
        return _conflicting(candidates)
    return _predicate_result(criterion, predicate_values.pop(), candidates)


def evaluate_recency_window(
    criterion: AtomicCriterion,
    rule: RecencyWindowRule,
    facts: Iterable[PatientFact],
    *,
    as_of: date,
) -> CriterionEvaluation:
    """Evaluate recency without assigning a date to a partially dated FHIR event."""
    candidates = _facts_for_exact_code(
        facts, kind=rule.fact_kind, system=rule.code.system, code=rule.code.value
    )
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    if not candidates:
        return _unknown("missing_evidence")
    if any(fact.effective_at is None for fact in candidates):
        return _unknown("missing_date", candidates)
    dates = [fact.effective_at.date() for fact in candidates if fact.effective_at]
    if any(event_date > as_of for event_date in dates):
        return _unknown("future_date", candidates)
    predicate_values = {
        (as_of - event_date).days <= rule.within_days for event_date in dates
    }
    if len(predicate_values) != 1:
        return _conflicting(candidates)
    return _predicate_result(criterion, predicate_values.pop(), candidates)


def evaluate_medication_status(
    criterion: AtomicCriterion,
    rule: MedicationStatusRule,
    facts: Iterable[PatientFact],
) -> CriterionEvaluation:
    """Compare one recorded medication status without inferring a status."""
    candidates = _facts_for_exact_code(
        facts, kind="medication", system=rule.code.system, code=rule.code.value
    )
    statuses = _documented_statuses(candidates, fact_kind="medication")
    if isinstance(statuses, CriterionEvaluation):
        return statuses
    return _status_result(criterion, rule.expected_status, candidates, statuses)


def evaluate_procedure_status(
    criterion: AtomicCriterion,
    rule: ProcedureStatusRule,
    facts: Iterable[PatientFact],
) -> CriterionEvaluation:
    """Compare one recorded Procedure status without inferring completion."""
    candidates = _facts_for_exact_code(
        facts, kind="procedure", system=rule.code.system, code=rule.code.value
    )
    statuses = _documented_statuses(candidates, fact_kind="procedure")
    if isinstance(statuses, CriterionEvaluation):
        return statuses
    return _status_result(criterion, rule.expected_status, candidates, statuses)


def _documented_statuses(
    candidates: tuple[PatientFact, ...],
    *,
    fact_kind: Literal["medication", "procedure"],
) -> set[str] | CriterionEvaluation:
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    if not candidates:
        return _unknown("missing_evidence")
    statuses: set[str] = set()
    for fact in candidates:
        if fact_kind == "medication":
            if not isinstance(fact.value, MedicationFactValue) or not fact.value.status:
                return _unknown("undocumented_status", candidates)
            statuses.add(fact.value.status)
            continue
        if not isinstance(fact.value, ProcedureFactValue) or not fact.value.status:
            return _unknown("undocumented_status", candidates)
        statuses.add(fact.value.status)
    return statuses


def _status_result(
    criterion: AtomicCriterion,
    expected_status: str,
    candidates: tuple[PatientFact, ...],
    statuses: set[str],
) -> CriterionEvaluation:
    predicate_values = {status == expected_status for status in statuses}
    if len(predicate_values) != 1:
        return _conflicting(candidates)
    return _predicate_result(criterion, predicate_values.pop(), candidates)


def _facts_for_exact_code(
    facts: Iterable[PatientFact], *, kind: str, system: str, code: str
) -> tuple[PatientFact, ...]:
    return tuple(
        fact
        for fact in facts
        if fact.kind == kind and fact.code.system == system and fact.code.value == code
    )


def _quality_result(facts: tuple[PatientFact, ...]) -> CriterionEvaluation | None:
    issue_codes = {issue.code for fact in facts for issue in fact.quality_issues}
    if "conflicting" in issue_codes:
        return _conflicting(facts)
    if issue_codes:
        return _unknown("unusable_evidence", facts)
    return None


def _predicate_result(
    criterion: AtomicCriterion,
    predicate_matched: bool,
    facts: tuple[PatientFact, ...],
) -> CriterionEvaluation:
    outcome_is_met = predicate_matched == (criterion.category == "inclusion")
    return CriterionEvaluation(
        outcome="met" if outcome_is_met else "not_met",
        evidence_fact_ids=[fact.fact_id for fact in facts],
        reason="predicate_matched" if predicate_matched else "predicate_not_matched",
        requires_review=False,
    )


def _unknown(
    reason: CriterionEvaluationReason, facts: tuple[PatientFact, ...] = ()
) -> CriterionEvaluation:
    return CriterionEvaluation(
        outcome="unknown",
        evidence_fact_ids=[fact.fact_id for fact in facts],
        reason=reason,
        requires_review=True,
    )


def _conflicting(facts: tuple[PatientFact, ...]) -> CriterionEvaluation:
    return CriterionEvaluation(
        outcome="conflicting",
        evidence_fact_ids=[fact.fact_id for fact in facts],
        reason="conflicting_evidence",
        requires_review=True,
    )
