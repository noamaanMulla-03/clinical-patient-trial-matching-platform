"""Manual criterion persistence while automated eligibility parsing is unavailable."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.criteria.schemas import AtomicCriterion
from src.db.models import Criterion, TrialVersion
from src.trials.extraction import extract_trial_fields


class ManualCriterionError(ValueError):
    """Raised when a hand-authored criterion cannot be tied to source evidence."""


def atomic_criterion_from_record(record: Criterion) -> AtomicCriterion:
    """Rebuild the exact typed rule stored alongside an immutable source span."""
    try:
        return AtomicCriterion.model_validate(
            {
                "category": record.category,
                "source_text": record.source_text,
                "source_start": record.source_start,
                "source_end": record.source_end,
                "rule": record.parsed_data,
            }
        )
    except ValidationError as error:
        raise ManualCriterionError(
            "Stored criterion contains invalid manual rule data."
        ) from error


async def create_manual_criteria(
    session: AsyncSession,
    *,
    trial_version_id: UUID,
    criteria: list[AtomicCriterion],
    parser_version: str,
) -> tuple[Criterion, ...]:
    """Persist manual atomic criteria only when their source spans are exact.

    This is intentionally a source-linked creation path, not a text parser. A future
    parser must produce the same AtomicCriterion boundary before it writes criteria.
    """
    if not parser_version.strip():
        raise ManualCriterionError("Manual criteria require a parser_version label.")
    trial_version = await session.get(TrialVersion, trial_version_id)
    if trial_version is None:
        raise ManualCriterionError("Trial version was not found for manual criteria.")
    eligibility_text = extract_trial_fields(trial_version.raw_study).eligibility_text
    if eligibility_text is None:
        raise ManualCriterionError("Trial version has no eligibility text to annotate.")

    records: list[Criterion] = []
    for criterion in criteria:
        if (
            criterion.source_end > len(eligibility_text)
            or eligibility_text[criterion.source_start : criterion.source_end]
            != criterion.source_text
        ):
            raise ManualCriterionError(
                "Manual criterion source span does not match trial eligibility text."
            )
        record = Criterion(
            id=uuid4(),
            trial_version_id=trial_version.id,
            category=criterion.category,
            source_text=criterion.source_text,
            source_start=criterion.source_start,
            source_end=criterion.source_end,
            parsed_data=criterion.rule.model_dump(mode="json"),
            parser_version=parser_version.strip(),
        )
        session.add(record)
        records.append(record)
    await session.flush()
    return tuple(records)
