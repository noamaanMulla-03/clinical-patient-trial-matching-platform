"""Build lexical retrieval inputs from one immutable normalized patient snapshot."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal, cast

from app.fhir.schemas import (
    ConditionFactValue,
    MedicationFactValue,
    PatientFact,
    ProcedureFactValue,
)
from app.retrieval.schemas import (
    PatientDerivedRetrievalQuery,
    RetrievalTerm,
    TrialMetadataFilters,
)

_BIRTH_DATE_CODE = "birth-date"
_RECORDED_SEX_CODE = "administrative-gender"


def build_patient_retrieval_query(
    facts: Iterable[PatientFact], *, as_of: date
) -> PatientDerivedRetrievalQuery:
    """Build retrieval inputs only from documented, usable, current patient facts.

    The result intentionally omits uncertain demographics and clinical concepts. An
    omitted filter broadens candidate retrieval; guessing would silently remove trials.
    """
    materialized_facts = tuple(facts)
    terms: list[RetrievalTerm] = []
    active_condition_labels: list[str] = []
    for fact in materialized_facts:
        if fact.quality_issues:
            continue
        if (
            fact.kind == "condition"
            and isinstance(fact.value, ConditionFactValue)
            and fact.value.clinical_status == "active"
        ):
            terms.append(_term_from_fact(fact, kind="condition"))
            if fact.code.display:
                active_condition_labels.append(fact.code.display)
        elif (
            fact.kind == "medication"
            and isinstance(fact.value, MedicationFactValue)
            and fact.value.status == "active"
        ):
            terms.append(_term_from_fact(fact, kind="medication"))
        elif (
            fact.kind == "procedure"
            and isinstance(fact.value, ProcedureFactValue)
            and fact.value.status == "completed"
        ):
            terms.append(_term_from_fact(fact, kind="procedure"))

    return PatientDerivedRetrievalQuery(
        terms=terms,
        filters=TrialMetadataFilters(
            conditions=active_condition_labels,
            age_years=_documented_age(materialized_facts, as_of=as_of),
            recorded_sex=_documented_recorded_sex(materialized_facts),
        ),
    )


def _term_from_fact(
    fact: PatientFact, *, kind: Literal["condition", "medication", "procedure"]
) -> RetrievalTerm:
    """Use display text when available while retaining a coded fallback for recall."""
    return RetrievalTerm(
        text=fact.code.display or fact.code.value,
        source_fact_id=fact.fact_id,
        kind=kind,
    )


def _documented_age(facts: tuple[PatientFact, ...], *, as_of: date) -> int | None:
    birth_dates = {
        fact.value
        for fact in facts
        if not fact.quality_issues
        and fact.kind == "demographic"
        and fact.code.value == _BIRTH_DATE_CODE
        and isinstance(fact.value, str)
        and len(fact.value) == 10
    }
    if len(birth_dates) != 1:
        return None
    try:
        birth_date = date.fromisoformat(next(iter(birth_dates)))
    except ValueError:
        return None
    return (
        as_of.year
        - birth_date.year
        - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
    )


def _documented_recorded_sex(
    facts: tuple[PatientFact, ...],
) -> Literal["male", "female"] | None:
    values = {
        fact.value.strip().lower()
        for fact in facts
        if not fact.quality_issues
        and fact.kind == "demographic"
        and fact.code.value == _RECORDED_SEX_CODE
        and isinstance(fact.value, str)
        and fact.value.strip().lower() in {"male", "female"}
    }
    return (
        cast(Literal["male", "female"], next(iter(values)))
        if len(values) == 1
        else None
    )
