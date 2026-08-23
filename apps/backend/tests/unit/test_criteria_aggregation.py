"""Bounded aggregate outcomes for deterministic criterion results."""

from uuid import uuid4

from app.criteria.aggregation import _outcome_for
from app.db.models import Criterion, CriterionResult


def _criterion(*, category: str) -> Criterion:
    return Criterion(id=uuid4(), category=category)


def _result(
    criterion: Criterion, *, outcome: str, requires_review: bool = False
) -> CriterionResult:
    return CriterionResult(
        id=uuid4(),
        criterion_id=criterion.id,
        outcome=outcome,
        requires_review=requires_review,
    )


def test_aggregation_uses_only_the_four_bounded_match_states() -> None:
    inclusion = _criterion(category="inclusion")
    exclusion = _criterion(category="exclusion")

    assert _outcome_for([inclusion], [_result(inclusion, outcome="met")]) == (
        "potential_match"
    )
    assert _outcome_for([inclusion], [_result(inclusion, outcome="not_met")]) == (
        "not_relevant"
    )
    assert _outcome_for([exclusion], [_result(exclusion, outcome="not_met")]) == (
        "likely_excluded"
    )
    assert (
        _outcome_for(
            [inclusion], [_result(inclusion, outcome="unknown", requires_review=True)]
        )
        == "needs_review"
    )
    assert _outcome_for([inclusion], []) == "needs_review"
