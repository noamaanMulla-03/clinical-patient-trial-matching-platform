"""Conservative aggregation of source-grounded atomic criterion results."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Criterion, CriterionResult, TrialMatch


class MatchAggregationError(ValueError):
    """Raised when a trial match cannot be safely aggregated."""


async def aggregate_trial_match(
    session: AsyncSession, *, trial_match_id: UUID
) -> TrialMatch:
    """Set the one bounded match outcome without making an enrollment decision.

    A partial, unknown, or conflicting evaluation cannot become a reassuring match.
    An established exclusion takes precedence over an unmet inclusion requirement.
    """
    trial_match = await session.get(TrialMatch, trial_match_id)
    if trial_match is None:
        raise MatchAggregationError("Trial match was not found.")
    criteria = (
        await session.scalars(
            select(Criterion).where(
                Criterion.trial_version_id == trial_match.trial_version_id
            )
        )
    ).all()
    results = (
        await session.scalars(
            select(CriterionResult).where(
                CriterionResult.trial_match_id == trial_match.id
            )
        )
    ).all()
    outcome = _outcome_for(criteria, results)
    trial_match.outcome = outcome
    trial_match.evaluated_at = datetime.now(UTC)
    await session.flush()
    return trial_match


def _outcome_for(
    criteria: Sequence[Criterion], results: Sequence[CriterionResult]
) -> str:
    """Aggregate only a complete set of result records for one trial version."""
    criterion_categories = {criterion.id: criterion.category for criterion in criteria}
    result_ids = {result.criterion_id for result in results}
    if (
        not criteria
        or result_ids != set(criterion_categories)
        or any(
            result.requires_review or result.outcome in {"unknown", "conflicting"}
            for result in results
        )
    ):
        return "needs_review"
    if any(
        criterion_categories[result.criterion_id] == "exclusion"
        and result.outcome == "not_met"
        for result in results
    ):
        return "likely_excluded"
    if any(
        criterion_categories[result.criterion_id] == "inclusion"
        and result.outcome == "not_met"
        for result in results
    ):
        return "not_relevant"
    return "potential_match"
