"""Safe metadata filtering after lexical candidate retrieval."""

from __future__ import annotations

from typing import Any

from src.retrieval.schemas import TrialMetadataFilters, TrialSearchMetadata
from src.retrieval.trial_documents import SearchableTrial


def metadata_from_trial(trial: SearchableTrial) -> TrialSearchMetadata:
    """Project only fields used by metadata filtering from one source version."""
    interventions = [
        intervention
        for intervention in trial.interventions
        if isinstance(intervention, dict)
    ]
    locations = [location for location in trial.locations if isinstance(location, dict)]
    return TrialSearchMetadata(
        conditions=trial.conditions,
        minimum_age=trial.minimum_age,
        maximum_age=trial.maximum_age,
        sex=trial.sex,
        status=trial.status,
        phases=trial.phases,
        countries=_text_values(locations, "country"),
        intervention_types=_text_values(interventions, "type"),
    )


def trial_matches_metadata(
    metadata: TrialSearchMetadata, filters: TrialMetadataFilters
) -> bool:
    """Reject documented incompatibilities; keep missing trial metadata reviewable.

    Status, phase, country, and intervention type are caller-selected operational
    filters, so they remain strict. Patient-derived facts are broad-recall signals,
    not eligibility gates; criterion evaluation assesses those semantics later.
    """
    return _requested_trial_metadata_matches(metadata, filters)


def _requested_trial_metadata_matches(
    metadata: TrialSearchMetadata, filters: TrialMetadataFilters
) -> bool:
    if filters.study_statuses and not _contains(
        filters.study_statuses, metadata.status
    ):
        return False
    if filters.phases and not _intersects(filters.phases, metadata.phases):
        return False
    if filters.countries and not _intersects(filters.countries, metadata.countries):
        return False
    return not filters.intervention_types or _intersects(
        filters.intervention_types, metadata.intervention_types
    )


def _contains(values: list[str], value: str | None) -> bool:
    return value is not None and value.casefold() in {
        item.casefold() for item in values
    }


def _intersects(left: list[str], right: list[str]) -> bool:
    return bool(
        {value.casefold() for value in left} & {value.casefold() for value in right}
    )


def _text_values(records: list[dict[str, Any]], field_name: str) -> list[str]:
    return [
        value
        for record in records
        if isinstance(value := record.get(field_name), str) and value.strip()
    ]
