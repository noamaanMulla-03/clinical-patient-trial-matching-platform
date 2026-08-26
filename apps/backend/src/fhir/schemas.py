"""Pydantic contracts for the FHIR import API boundary."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from src.fhir.safety import SyntheticDataMarkerError, require_synthetic_fhir_bundle

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


class ClinicalCode(BaseModel):
    """A coded clinical concept retained from the FHIR source resource."""

    model_config = ConfigDict(extra="forbid")

    system: str = Field(min_length=1)
    value: str = Field(min_length=1)
    display: str | None = None

    @field_validator("system", "value", mode="before")
    @classmethod
    def normalize_required_coding_text(cls, value: Any) -> Any:
        """Trim identifier padding while the raw coding remains in source_resource."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("display", mode="before")
    @classmethod
    def normalize_display_text(cls, value: Any) -> Any:
        """Keep display labels readable without changing their source snapshot."""
        return " ".join(value.split()) if isinstance(value, str) else value


class FHIRProvenance(BaseModel):
    """The exact FHIR resource that produced a normalized patient fact."""

    model_config = ConfigDict(extra="forbid")

    resource_type: FHIRResourceType
    resource_id: str = Field(min_length=1)
    version_id: str | None = Field(
        default=None,
        description="FHIR meta.versionId when supplied by the source resource.",
    )


class DateNormalization(BaseModel):
    """A parsed FHIR date that retains precision instead of inventing missing parts."""

    model_config = ConfigDict(extra="forbid")

    source_value: str = Field(min_length=1)
    precision: Literal["year", "month", "day", "datetime"]
    normalized_date: date | None = None
    normalized_at: AwareDatetime | None = None


class QuantityNormalization(BaseModel):
    """A numeric FHIR Quantity with cleaned identifiers but no unit conversion."""

    model_config = ConfigDict(extra="forbid")

    value: int | float
    unit: str | None = Field(default=None, min_length=1)
    system: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1)

    @field_validator("unit", "system", "code", mode="before")
    @classmethod
    def normalize_quantity_text(cls, value: Any) -> Any:
        """Keep quantity identifiers consistent without converting their meaning."""
        return value.strip() if isinstance(value, str) else value


class FactNormalization(BaseModel):
    """Deterministic normalized fields separate from the immutable raw source."""

    model_config = ConfigDict(extra="forbid")

    date: DateNormalization | None = None
    quantity: QuantityNormalization | None = None


class DataQualityIssue(BaseModel):
    """An explicit non-diagnostic reason a fact may need review or cannot be used."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["missing", "stale", "invalid", "conflicting"]
    field: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ImportDataQualityIssue(DataQualityIssue):
    """An import-level issue linked to the source resource that caused it."""

    source: FHIRProvenance
    fact_id: str | None = Field(default=None, min_length=1)


class ConditionFactValue(BaseModel):
    """Condition details that cannot safely be represented as one scalar value."""

    model_config = ConfigDict(extra="forbid")

    clinical_status: str | None = None
    # Keep FHIR date/dateTime text unchanged because FHIR permits partial dates.
    onset_date: str | None = Field(default=None, min_length=1)


class ReferenceRangeQuantity(BaseModel):
    """A numeric FHIR Quantity retained without changing its supplied unit."""

    model_config = ConfigDict(extra="forbid")

    value: int | float
    unit: str | None = Field(default=None, min_length=1)


class ObservationReferenceRange(BaseModel):
    """The recorded reference range associated with a numeric observation."""

    model_config = ConfigDict(extra="forbid")

    low: ReferenceRangeQuantity | None = None
    high: ReferenceRangeQuantity | None = None
    text: str | None = Field(default=None, min_length=1)


class ObservationFactValue(BaseModel):
    """Numeric observation data that is not represented by the shared fact fields."""

    model_config = ConfigDict(extra="forbid")

    numeric_value: int | float
    status: str | None = None
    # Preserve FHIR date/dateTime text because it may be partial or timezone-free.
    effective_date: str | None = Field(default=None, min_length=1)
    reference_ranges: list[ObservationReferenceRange] = Field(default_factory=list)


class MedicationFactValue(BaseModel):
    """Recorded medication status and source date for either medication resource."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    intent: str | None = None
    effective_date: str | None = Field(default=None, min_length=1)


class ProcedureFactValue(BaseModel):
    """Recorded procedure status and performed date without inferred timing."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    performed_date: str | None = Field(default=None, min_length=1)


class AllergyFactValue(BaseModel):
    """Recorded allergy status values and creation date from the source resource."""

    model_config = ConfigDict(extra="forbid")

    clinical_status: str | None = None
    verification_status: str | None = None
    recorded_date: str | None = Field(default=None, min_length=1)


PatientFactValue = (
    str
    | int
    | float
    | bool
    | ConditionFactValue
    | ObservationFactValue
    | MedicationFactValue
    | ProcedureFactValue
    | AllergyFactValue
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
    # The source Bundle is also stored once per import; this copy makes each fact
    # independently reviewable through its fact id without reparsing the Bundle.
    source_resource: dict[str, Any]
    normalization: FactNormalization = Field(default_factory=FactNormalization)
    quality_issues: list[DataQualityIssue] = Field(default_factory=list)

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value: Any) -> Any:
        """Remove accidental padding without claiming cross-unit equivalence."""
        return value.strip() if isinstance(value, str) else value


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
    data_quality_issues: list[ImportDataQualityIssue]


class PatientImportSnapshotResponse(BaseModel):
    """The immutable import snapshot selected for one patient timeline response."""

    id: UUID
    fhir_version: str
    source_hash: str
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    data_quality_issues: list[ImportDataQualityIssue]


class PatientFactResponse(BaseModel):
    """A persisted normalized fact with independently reviewable source evidence."""

    fact_id: str
    kind: PatientFactKind
    code: ClinicalCode
    value: PatientFactValue | None = None
    unit: str | None = None
    effective_at: AwareDatetime | None = None
    source: FHIRProvenance
    source_resource: dict[str, Any]
    normalization: FactNormalization
    quality_issues: list[DataQualityIssue]


class PatientFactSourceResponse(BaseModel):
    """The immutable source resource for a fact in the latest completed import."""

    patient_id: str
    fact_id: str
    source: FHIRProvenance
    source_resource: dict[str, Any]


class PatientTimelineResponse(BaseModel):
    """The latest completed synthetic import and its unmerged fact timeline."""

    patient_id: str
    synthetic: bool
    import_snapshot: PatientImportSnapshotResponse | None
    facts: list[PatientFactResponse]
