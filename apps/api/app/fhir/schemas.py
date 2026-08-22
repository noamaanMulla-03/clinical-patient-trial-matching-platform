"""Pydantic contracts for the FHIR import API boundary."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.fhir.safety import SyntheticDataMarkerError, require_synthetic_fhir_bundle

FHIR_IMPORT_INVALID_BUNDLE_CODE = "fhir_import.invalid_bundle"
FHIRResourceType = Literal[
    "Patient",
    "Condition",
    "Observation",
    "MedicationStatement",
    "MedicationRequest",
    "Procedure",
    "AllergyIntolerance",
]
PatientFactKind = Literal[
    "demographic",
    "condition",
    "observation",
    "medication",
    "procedure",
    "allergy",
]
PatientFactValue = str | int | float | bool


class ClinicalCode(BaseModel):
    """A coded clinical concept retained from the FHIR source resource."""

    model_config = ConfigDict(extra="forbid")

    system: str = Field(min_length=1)
    value: str = Field(min_length=1)
    display: str | None = None


class FHIRProvenance(BaseModel):
    """The exact FHIR resource that produced a normalized patient fact."""

    model_config = ConfigDict(extra="forbid")

    resource_type: FHIRResourceType
    resource_id: str = Field(min_length=1)
    version_id: str | None = Field(
        default=None,
        description="FHIR meta.versionId when supplied by the source resource.",
    )


class PatientFact(BaseModel):
    """A source-linked clinical fact normalized from an allowed FHIR resource."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    kind: PatientFactKind
    code: ClinicalCode
    value: PatientFactValue | None = None
    unit: str | None = Field(default=None, min_length=1)
    effective_at: AwareDatetime | None = None
    source: FHIRProvenance


class FHIRImportRequest(BaseModel):
    """A synthetic FHIR Bundle submitted for import."""

    model_config = ConfigDict(extra="forbid")

    bundle: dict[str, Any] = Field(
        description="The complete synthetic FHIR Bundle to validate and import."
    )

    @field_validator("bundle")
    @classmethod
    def require_synthetic_bundle(cls, bundle: dict[str, Any]) -> dict[str, Any]:
        """Reject content before any importer can persist, queue, or log it."""
        try:
            require_synthetic_fhir_bundle(bundle)
        except SyntheticDataMarkerError as error:
            # Do not expose submitted clinical content in the validation response.
            raise PydanticCustomError(
                FHIR_IMPORT_INVALID_BUNDLE_CODE,
                "FHIR import requires a synthetically marked Bundle.",
            ) from error
        return bundle


class FHIRImportResponse(BaseModel):
    """Operational result of a persisted synthetic FHIR patient import."""

    patient_id: str
    patient_import_id: UUID
    fact_ids: list[str]
