"""Immutable, searchable public-trial views for one trial source version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.db.models import TrialVersion
from src.trials.extraction import TrialExtractionError, extract_trial_fields


class SearchableTrial(Protocol):
    """Fields shared by current projections and immutable version views."""

    @property
    def nct_id(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def conditions(self) -> list[str]: ...

    @property
    def interventions(self) -> list[dict[str, Any]]: ...

    @property
    def status(self) -> str | None: ...

    @property
    def phases(self) -> list[str]: ...

    @property
    def eligibility_text(self) -> str | None: ...

    @property
    def minimum_age(self) -> str | None: ...

    @property
    def maximum_age(self) -> str | None: ...

    @property
    def sex(self) -> str | None: ...

    @property
    def locations(self) -> list[dict[str, Any]]: ...


class TrialDocumentError(ValueError):
    """Raised when an immutable trial version cannot supply search fields."""


@dataclass(frozen=True, slots=True)
class TrialSearchDocument:
    """Immutable in-memory public-trial fields shared by retrieval callers."""

    nct_id: str
    title: str | None
    conditions: list[str]
    interventions: list[dict[str, Any]]
    status: str | None
    phases: list[str]
    eligibility_text: str | None
    minimum_age: str | None
    maximum_age: str | None
    sex: str | None
    locations: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class TrialVersionDocument(TrialSearchDocument):
    """Searchable fields derived from the exact immutable source version used."""

    trial_version_id: UUID


def document_from_trial_version(version: TrialVersion) -> TrialVersionDocument:
    """Derive query fields from source evidence rather than a mutable projection."""
    try:
        fields = extract_trial_fields(version.raw_study)
    except TrialExtractionError as error:
        raise TrialDocumentError(
            "Trial version cannot supply valid searchable public fields."
        ) from error
    return TrialVersionDocument(
        trial_version_id=version.id,
        nct_id=fields.nct_id,
        title=fields.title,
        conditions=fields.conditions,
        interventions=[
            intervention.model_dump(mode="json")
            for intervention in fields.interventions
        ],
        status=fields.status,
        phases=fields.phases,
        eligibility_text=fields.eligibility_text,
        minimum_age=fields.minimum_age,
        maximum_age=fields.maximum_age,
        sex=fields.sex,
        locations=[location.model_dump(mode="json") for location in fields.locations],
    )
