"""Extract matching-relevant fields from an unmodified ClinicalTrials.gov v2 study."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class TrialExtractionError(ValueError):
    """Raised when a source study cannot safely provide its required identity."""


@dataclass(frozen=True, slots=True)
class SourceUpdateTime:
    """Public source-update timing and its explicit extraction quality state."""

    value: datetime | None
    state: str


class TrialIntervention(BaseModel):
    """A source-derived intervention retained for current searchable trial state."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str | None = None
    description: str | None = None
    other_names: list[str] = Field(default_factory=list)


class TrialLocation(BaseModel):
    """Public site fields useful for advisory geographical filtering only."""

    model_config = ConfigDict(extra="forbid")

    facility: str | None = None
    status: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class ExtractedTrialFields(BaseModel):
    """Deterministic, current searchable fields linked to an immutable raw study."""

    model_config = ConfigDict(extra="forbid")

    nct_id: str = Field(pattern=r"^NCT\d{8}$")
    title: str | None = None
    conditions: list[str] = Field(default_factory=list)
    interventions: list[TrialIntervention] = Field(default_factory=list)
    status: str | None = None
    phases: list[str] = Field(default_factory=list)
    eligibility_text: str | None = None
    minimum_age: str | None = None
    maximum_age: str | None = None
    sex: str | None = None
    locations: list[TrialLocation] = Field(default_factory=list)


def extract_trial_fields(study: Mapping[str, Any]) -> ExtractedTrialFields:
    """Select documented v2 protocol fields without mutating the raw source study.

    Missing optional modules remain absent or empty. A malformed present module fails
    ingestion instead of being silently treated as an unavailable clinical criterion.
    """
    protocol_section = _required_mapping(study, "protocolSection")
    identification = _required_mapping(protocol_section, "identificationModule")
    nct_id = _required_nct_id(identification)
    eligibility = _optional_mapping(protocol_section, "eligibilityModule")

    return ExtractedTrialFields(
        nct_id=nct_id,
        title=_optional_text(identification, "briefTitle")
        or _optional_text(identification, "officialTitle"),
        conditions=_optional_text_list(
            _optional_mapping(protocol_section, "conditionsModule"), "conditions"
        ),
        interventions=_interventions(
            _optional_mapping(protocol_section, "armsInterventionsModule")
        ),
        status=_optional_text(
            _optional_mapping(protocol_section, "statusModule"), "overallStatus"
        ),
        phases=_optional_text_list(
            _optional_mapping(protocol_section, "designModule"), "phases"
        ),
        # Keep the extracted criteria text byte-for-byte equivalent to the source so
        # later criterion spans can be traced back to raw_study without ambiguity.
        eligibility_text=_optional_text(eligibility, "eligibilityCriteria"),
        minimum_age=_optional_text(eligibility, "minimumAge"),
        maximum_age=_optional_text(eligibility, "maximumAge"),
        sex=_optional_text(eligibility, "sex"),
        locations=_locations(
            _optional_mapping(protocol_section, "contactsLocationsModule")
        ),
    )


def extract_source_update_time(study: Mapping[str, Any]) -> SourceUpdateTime:
    """Read the public last-posted date without treating missing data as current."""
    protocol_section = study.get("protocolSection")
    if not isinstance(protocol_section, Mapping):
        return SourceUpdateTime(value=None, state="invalid")
    status_module = protocol_section.get("statusModule")
    if status_module is None:
        return SourceUpdateTime(value=None, state="missing")
    if not isinstance(status_module, Mapping):
        return SourceUpdateTime(value=None, state="invalid")
    last_update = status_module.get("lastUpdatePostDateStruct")
    if last_update is None:
        return SourceUpdateTime(value=None, state="missing")
    if not isinstance(last_update, Mapping):
        return SourceUpdateTime(value=None, state="invalid")
    source_date = last_update.get("date")
    if not isinstance(source_date, str):
        return SourceUpdateTime(value=None, state="invalid")
    try:
        return SourceUpdateTime(
            value=datetime.combine(
                date.fromisoformat(source_date), datetime.min.time(), UTC
            ),
            state="available",
        )
    except ValueError:
        return SourceUpdateTime(value=None, state="invalid")


def _interventions(module: Mapping[str, Any] | None) -> list[TrialIntervention]:
    interventions: list[TrialIntervention] = []
    for intervention in _optional_mapping_list(module, "interventions"):
        name = _optional_text(intervention, "name")
        if name is None:
            raise TrialExtractionError(
                "ClinicalTrials.gov intervention is missing its required name."
            )
        interventions.append(
            TrialIntervention(
                name=name,
                type=_optional_text(intervention, "type"),
                description=_optional_text(intervention, "description"),
                other_names=_optional_text_list(intervention, "otherNames"),
            )
        )
    return interventions


def _locations(module: Mapping[str, Any] | None) -> list[TrialLocation]:
    return [
        TrialLocation(
            facility=_optional_text(location, "facility"),
            status=_optional_text(location, "status"),
            city=_optional_text(location, "city"),
            state=_optional_text(location, "state"),
            postal_code=_optional_text(location, "zip"),
            country=_optional_text(location, "country"),
        )
        for location in _optional_mapping_list(module, "locations")
    ]


def _required_nct_id(identification: Mapping[str, Any]) -> str:
    nct_id = _optional_text(identification, "nctId")
    if nct_id is None or not _NCT_ID_PATTERN.fullmatch(nct_id):
        raise TrialExtractionError(
            "ClinicalTrials.gov study is missing a valid NCT identifier."
        )
    return nct_id


def _required_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    nested_value = value.get(field_name)
    if not isinstance(nested_value, Mapping):
        raise TrialExtractionError(
            f"ClinicalTrials.gov study is missing its {field_name} object."
        )
    return nested_value


def _optional_mapping(
    value: Mapping[str, Any], field_name: str
) -> Mapping[str, Any] | None:
    nested_value = value.get(field_name)
    if nested_value is None:
        return None
    if not isinstance(nested_value, Mapping):
        raise TrialExtractionError(
            f"ClinicalTrials.gov {field_name} must be an object when present."
        )
    return nested_value


def _optional_mapping_list(
    value: Mapping[str, Any] | None, field_name: str
) -> list[Mapping[str, Any]]:
    if value is None or field_name not in value:
        return []
    list_value = value[field_name]
    if not isinstance(list_value, list) or not all(
        isinstance(item, Mapping) for item in list_value
    ):
        raise TrialExtractionError(
            f"ClinicalTrials.gov {field_name} must be an array of objects when present."
        )
    return list_value


def _optional_text_list(value: Mapping[str, Any] | None, field_name: str) -> list[str]:
    if value is None or field_name not in value:
        return []
    list_value = value[field_name]
    if not isinstance(list_value, list) or not all(
        isinstance(item, str) and item for item in list_value
    ):
        raise TrialExtractionError(
            f"ClinicalTrials.gov {field_name} must be an array of text when present."
        )
    return list(list_value)


def _optional_text(value: Mapping[str, Any] | None, field_name: str) -> str | None:
    if value is None or field_name not in value:
        return None
    text_value = value[field_name]
    if not isinstance(text_value, str) or not text_value:
        raise TrialExtractionError(
            f"ClinicalTrials.gov {field_name} must be non-empty text when present."
        )
    return text_value
