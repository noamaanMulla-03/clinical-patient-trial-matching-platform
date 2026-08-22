"""Minimal, source-preserving normalization for synthetic FHIR Patient resources."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.fhir.schemas import ClinicalCode, FHIRProvenance, PatientFact

FHIR_R4_VERSION = "R4"
_FHIR_ID_PATTERN = re.compile(r"[A-Za-z0-9-.]{1,64}")
_PATIENT_STRUCTURE_SYSTEM = "http://hl7.org/fhir/StructureDefinition/Patient"


class FHIRPatientNormalizationError(ValueError):
    """Raised when a marked Bundle lacks one valid Patient resource."""


@dataclass(frozen=True, slots=True)
class NormalizedPatient:
    """A patient identity with the facts safely available from its source resource."""

    patient_id: str
    facts: tuple[PatientFact, ...]


def normalize_patient_resource(
    bundle: Mapping[str, Any], *, patient_import_id: UUID
) -> NormalizedPatient:
    """Normalize only recorded Patient demographics and preserve their FHIR provenance.

    Birth dates are retained as source values instead of coerced to timestamps because
    FHIR permits partial dates. Absent demographics produce no fact.
    """
    patient_resource = _single_patient_resource(bundle)
    patient_id = _resource_id(patient_resource)
    provenance = FHIRProvenance(
        resource_type="Patient",
        resource_id=patient_id,
        version_id=_resource_version_id(patient_resource),
    )
    facts: list[PatientFact] = []

    gender = patient_resource.get("gender")
    if isinstance(gender, str) and gender.strip():
        facts.append(
            PatientFact(
                fact_id=_fact_id(
                    patient_import_id, patient_id, "administrative-gender"
                ),
                patient_id=patient_id,
                kind="demographic",
                code=ClinicalCode(
                    system=_PATIENT_STRUCTURE_SYSTEM,
                    value="administrative-gender",
                ),
                value=gender,
                source=provenance,
            )
        )

    birth_date = patient_resource.get("birthDate")
    if isinstance(birth_date, str) and birth_date.strip():
        facts.append(
            PatientFact(
                fact_id=_fact_id(patient_import_id, patient_id, "birth-date"),
                patient_id=patient_id,
                kind="demographic",
                code=ClinicalCode(system=_PATIENT_STRUCTURE_SYSTEM, value="birth-date"),
                value=birth_date,
                source=provenance,
            )
        )

    return NormalizedPatient(patient_id=patient_id, facts=tuple(facts))


def _single_patient_resource(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise FHIRPatientNormalizationError(
            "FHIR Bundle must contain one Patient resource."
        )

    patients = [
        resource
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(resource := entry.get("resource"), Mapping)
        and resource.get("resourceType") == "Patient"
    ]
    if len(patients) != 1:
        raise FHIRPatientNormalizationError(
            "FHIR Bundle must contain exactly one Patient resource."
        )
    return patients[0]


def _resource_id(resource: Mapping[str, Any]) -> str:
    resource_id = resource.get("id")
    if not isinstance(resource_id, str) or not _FHIR_ID_PATTERN.fullmatch(resource_id):
        raise FHIRPatientNormalizationError(
            "FHIR Patient resource must have a valid id."
        )
    return resource_id


def _resource_version_id(resource: Mapping[str, Any]) -> str | None:
    meta = resource.get("meta")
    if not isinstance(meta, Mapping):
        return None
    version_id = meta.get("versionId")
    return version_id if isinstance(version_id, str) and version_id.strip() else None


def _fact_id(patient_import_id: UUID, patient_id: str, property_name: str) -> str:
    """Make every normalized fact unique to its immutable import snapshot."""
    return f"{patient_import_id}:Patient:{patient_id}:{property_name}"
