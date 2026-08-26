"""Typed query and metadata contracts for conservative trial retrieval."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalTerm(BaseModel):
    """One searchable term with the patient fact that supplied it."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    source_fact_id: str = Field(min_length=1)
    kind: Literal["condition", "medication", "procedure"]


class TrialMetadataFilters(BaseModel):
    """Metadata filters applied without turning unknown patient data into exclusions."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[str] = Field(default_factory=list)
    age_years: int | None = Field(default=None, ge=0, le=130)
    recorded_sex: Literal["male", "female"] | None = None
    study_statuses: list[str] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    intervention_types: list[str] = Field(default_factory=list)

    @field_validator(
        "conditions",
        "study_statuses",
        "phases",
        "countries",
        "intervention_types",
        mode="before",
    )
    @classmethod
    def normalize_filter_texts(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return sorted(
            {
                " ".join(item.split())
                for item in value
                if isinstance(item, str) and item.strip()
            }
        )


class PatientDerivedRetrievalQuery(BaseModel):
    """Patient-derived lexical query plus only documented demographic filters."""

    model_config = ConfigDict(extra="forbid")

    terms: list[RetrievalTerm] = Field(default_factory=list)
    filters: TrialMetadataFilters = Field(default_factory=TrialMetadataFilters)

    @property
    def lexical_text(self) -> str:
        """Return stable de-duplicated text suitable for PostgreSQL web search."""
        return " ".join(dict.fromkeys(term.text for term in self.terms))


class TrialSearchMetadata(BaseModel):
    """Current trial projection fields used by deterministic metadata filtering."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[str] = Field(default_factory=list)
    minimum_age: str | None = None
    maximum_age: str | None = None
    sex: str | None = None
    status: str | None = None
    phases: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    intervention_types: list[str] = Field(default_factory=list)
