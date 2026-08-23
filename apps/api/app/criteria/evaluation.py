"""Deterministic, source-evidence-only evaluation for the initial criterion set."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date

from app.criteria.advanced import (
    evaluate_date_window,
    evaluate_medication_status,
    evaluate_procedure_status,
    evaluate_recency_window,
)
from app.criteria.schemas import (
    AgeRule,
    AtomicCriterion,
    CodedConditionRule,
    CriterionEvaluation,
    CriterionEvaluationReason,
    DateWindowRule,
    MedicationStatusRule,
    NumericLabThresholdRule,
    RecencyWindowRule,
    RecordedSexRule,
)
from app.criteria.units import UnitCompatibilityError, convert_lab_value
from app.fhir.schemas import ObservationFactValue, PatientFact

_BIRTH_DATE_CODE = "birth-date"
_RECORDED_SEX_CODE = "administrative-gender"


def evaluate_atomic_criterion(
    criterion: AtomicCriterion,
    facts: Iterable[PatientFact],
    *,
    as_of: date,
) -> CriterionEvaluation:
    """Evaluate one rule with only recorded facts from one selected patient import."""
    materialized_facts = tuple(facts)
    rule = criterion.rule
    if isinstance(rule, AgeRule):
        return _evaluate_age(criterion, rule, materialized_facts, as_of)
    if isinstance(rule, RecordedSexRule):
        return _evaluate_recorded_sex(criterion, rule, materialized_facts)
    if isinstance(rule, CodedConditionRule):
        return _evaluate_coded_condition(criterion, rule, materialized_facts)
    if isinstance(rule, NumericLabThresholdRule):
        return _evaluate_numeric_lab(criterion, rule, materialized_facts)
    if isinstance(rule, DateWindowRule):
        return evaluate_date_window(criterion, rule, materialized_facts)
    if isinstance(rule, RecencyWindowRule):
        return evaluate_recency_window(criterion, rule, materialized_facts, as_of=as_of)
    if isinstance(rule, MedicationStatusRule):
        return evaluate_medication_status(criterion, rule, materialized_facts)
    return evaluate_procedure_status(criterion, rule, materialized_facts)


def _evaluate_age(
    criterion: AtomicCriterion,
    rule: AgeRule,
    facts: tuple[PatientFact, ...],
    as_of: date,
) -> CriterionEvaluation:
    candidates = _facts_for_code(facts, kind="demographic", code=_BIRTH_DATE_CODE)
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    if not candidates:
        return _unknown("missing_evidence")
    birth_date_ranges: set[tuple[date, date]] = set()
    for fact in candidates:
        if (birth_date_range := _birth_date_range(fact.value)) is None:
            return _unknown("unusable_evidence", candidates)
        birth_date_ranges.add(birth_date_range)
    if len(birth_date_ranges) != 1:
        return _conflicting(candidates)
    earliest_birth_date, latest_birth_date = next(iter(birth_date_ranges))
    youngest_age = _age_in_years(latest_birth_date, as_of)
    oldest_age = _age_in_years(earliest_birth_date, as_of)
    if rule.operator == "at_least":
        if youngest_age >= rule.years:
            return _predicate_result(criterion, True, candidates)
        if oldest_age < rule.years:
            return _predicate_result(criterion, False, candidates)
    else:
        if oldest_age <= rule.years:
            return _predicate_result(criterion, True, candidates)
        if youngest_age > rule.years:
            return _predicate_result(criterion, False, candidates)
    return _unknown("ambiguous_age", candidates)


def _evaluate_recorded_sex(
    criterion: AtomicCriterion,
    rule: RecordedSexRule,
    facts: tuple[PatientFact, ...],
) -> CriterionEvaluation:
    candidates = _facts_for_code(facts, kind="demographic", code=_RECORDED_SEX_CODE)
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    values = {
        fact.value.strip().lower()
        for fact in candidates
        if isinstance(fact.value, str) and fact.value.strip()
    }
    if not candidates:
        return _unknown("missing_evidence")
    if len(values) != 1 or len(values) != len(candidates):
        return _conflicting(candidates)
    recorded_value = next(iter(values))
    if recorded_value not in {"male", "female"}:
        return _unknown("unusable_evidence", candidates)
    return _predicate_result(criterion, recorded_value == rule.value, candidates)


def _evaluate_coded_condition(
    criterion: AtomicCriterion,
    rule: CodedConditionRule,
    facts: tuple[PatientFact, ...],
) -> CriterionEvaluation:
    candidates = _facts_for_exact_code(
        facts,
        kind="condition",
        system=rule.code.system,
        code=rule.code.value,
    )
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    # No Condition is not evidence that the condition is absent from a partial record.
    if not candidates:
        return _unknown("missing_evidence")
    return _predicate_result(criterion, True, candidates)


def _evaluate_numeric_lab(
    criterion: AtomicCriterion,
    rule: NumericLabThresholdRule,
    facts: tuple[PatientFact, ...],
) -> CriterionEvaluation:
    candidates = _facts_for_exact_code(
        facts,
        kind="observation",
        system=rule.code.system,
        code=rule.code.value,
    )
    quality_result = _quality_result(candidates)
    if quality_result is not None:
        return quality_result
    if not candidates:
        return _unknown("missing_evidence")
    values: list[float] = []
    for fact in candidates:
        if not isinstance(fact.value, ObservationFactValue) or fact.unit is None:
            return _unknown("unusable_evidence", candidates)
        try:
            values.append(
                convert_lab_value(
                    system=rule.code.system,
                    code=rule.code.value,
                    value=float(fact.value.numeric_value),
                    source_unit=fact.unit,
                    target_unit=rule.unit,
                )
            )
        except UnitCompatibilityError:
            return _unknown("unusable_evidence", candidates)
    predicate_values = {_compare(value, rule) for value in values}
    if len(predicate_values) != 1:
        return _conflicting(candidates)
    return _predicate_result(criterion, predicate_values.pop(), candidates)


def _facts_for_code(
    facts: tuple[PatientFact, ...], *, kind: str, code: str
) -> tuple[PatientFact, ...]:
    return tuple(
        fact for fact in facts if fact.kind == kind and fact.code.value == code
    )


def _facts_for_exact_code(
    facts: tuple[PatientFact, ...], kind: str, system: str, code: str
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


def _birth_date_range(value: object) -> tuple[date, date] | None:
    """Return the possible FHIR birth-date interval without inventing precision."""
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 4:
            year = int(value)
            return date(year, 1, 1), date(year, 12, 31)
        if len(value) == 7:
            year, month = (int(part) for part in value.split("-"))
            return date(year, month, 1), date(
                year, month, calendar.monthrange(year, month)[1]
            )
        parsed_date = date.fromisoformat(value)
        return parsed_date, parsed_date
    except ValueError:
        return None


def _age_in_years(birth_date: date, as_of: date) -> int:
    return (
        as_of.year
        - birth_date.year
        - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
    )


def _compare(value: float, rule: NumericLabThresholdRule) -> bool:
    return {
        ">": value > rule.threshold,
        ">=": value >= rule.threshold,
        "<": value < rule.threshold,
        "<=": value <= rule.threshold,
    }[rule.comparator]
