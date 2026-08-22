"""Minimal, source-preserving normalization for synthetic FHIR Patient resources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.fhir.schemas import (
    AllergyFactValue,
    ClinicalCode,
    ConditionFactValue,
    DataQualityIssue,
    DateNormalization,
    FactNormalization,
    FHIRProvenance,
    ImportDataQualityIssue,
    MedicationFactValue,
    ObservationFactValue,
    ObservationReferenceRange,
    PatientFact,
    PatientFactKind,
    ProcedureFactValue,
    QuantityNormalization,
    ReferenceRangeQuantity,
)

FHIR_R4_VERSION = "R4"
_FHIR_ID_PATTERN = re.compile(r"[A-Za-z0-9-.]{1,64}")
_FHIR_YEAR_PATTERN = re.compile(r"\d{4}")
_FHIR_MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")
_PATIENT_STRUCTURE_SYSTEM = "http://hl7.org/fhir/StructureDefinition/Patient"
_STALE_FACT_AGE = timedelta(days=365)


class FHIRPatientNormalizationError(ValueError):
    """Raised when a marked Bundle lacks one valid Patient resource."""


@dataclass(frozen=True, slots=True)
class NormalizedPatient:
    """A patient identity with the facts safely available from its source resource."""

    patient_id: str
    facts: tuple[PatientFact, ...]
    data_quality_issues: tuple[ImportDataQualityIssue, ...]


@dataclass(frozen=True, slots=True)
class PatientSource:
    """The source Patient resource and its valid intra-Bundle references."""

    resource: Mapping[str, Any]
    references: frozenset[str]


def normalize_patient_resource(
    bundle: Mapping[str, Any],
    *,
    patient_import_id: UUID,
    evaluated_at: datetime | None = None,
) -> NormalizedPatient:
    """Normalize only recorded Patient demographics and preserve their FHIR provenance.

    Birth dates are retained as source values instead of coerced to timestamps because
    FHIR permits partial dates. Absent demographics produce no fact.
    """
    patient_source = _single_patient_source(bundle)
    patient_resource = patient_source.resource
    patient_id = _resource_id(patient_resource, resource_type="Patient")
    provenance = FHIRProvenance(
        resource_type="Patient",
        resource_id=patient_id,
        version_id=_resource_version_id(patient_resource),
    )
    facts: list[PatientFact] = []
    import_issues = _patient_demographic_issues(patient_resource, provenance)

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
                source_resource=_source_resource_copy(patient_resource),
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
                source_resource=_source_resource_copy(patient_resource),
                normalization=FactNormalization(
                    date=_normalized_date(birth_date),
                ),
            )
        )

    facts.extend(
        _condition_facts(
            bundle,
            patient_id=patient_id,
            patient_references=patient_source.references,
            patient_import_id=patient_import_id,
        )
    )
    facts.extend(
        _observation_facts(
            bundle,
            patient_id=patient_id,
            patient_references=patient_source.references,
            patient_import_id=patient_import_id,
        )
    )
    facts.extend(
        _medication_facts(
            bundle,
            patient_id=patient_id,
            patient_references=patient_source.references,
            patient_import_id=patient_import_id,
        )
    )
    facts.extend(
        _procedure_facts(
            bundle,
            patient_id=patient_id,
            patient_references=patient_source.references,
            patient_import_id=patient_import_id,
        )
    )
    facts.extend(
        _allergy_facts(
            bundle,
            patient_id=patient_id,
            patient_references=patient_source.references,
            patient_import_id=patient_import_id,
        )
    )

    facts = _annotate_fact_quality(
        facts,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )
    import_issues.extend(_observation_value_issues(bundle, patient_source.references))
    import_issues.extend(_fact_quality_import_issues(facts))
    return NormalizedPatient(
        patient_id=patient_id,
        facts=tuple(facts),
        data_quality_issues=tuple(import_issues),
    )


def _single_patient_source(bundle: Mapping[str, Any]) -> PatientSource:
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise FHIRPatientNormalizationError(
            "FHIR Bundle must contain one Patient resource."
        )

    patient_entries = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(entry.get("resource"), Mapping)
        and entry["resource"].get("resourceType") == "Patient"
    ]
    if len(patient_entries) != 1:
        raise FHIRPatientNormalizationError(
            "FHIR Bundle must contain exactly one Patient resource."
        )
    patient_entry = patient_entries[0]
    patient_resource = patient_entry["resource"]
    patient_id = _resource_id(patient_resource, resource_type="Patient")
    references = {patient_id, f"Patient/{patient_id}"}
    full_url = patient_entry.get("fullUrl")
    if isinstance(full_url, str) and full_url.strip():
        references.add(full_url)
    return PatientSource(resource=patient_resource, references=frozenset(references))


def _condition_facts(
    bundle: Mapping[str, Any],
    *,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
) -> list[PatientFact]:
    """Normalize only Conditions linked to the Bundle's one Patient resource."""
    facts: list[PatientFact] = []
    for condition in _resources_of_type(bundle, "Condition"):
        onset_date = _string_field(condition, "onsetDateTime")
        facts.extend(
            _coded_resource_facts(
                condition,
                resource_type="Condition",
                kind="condition",
                patient_id=patient_id,
                patient_references=patient_references,
                patient_import_id=patient_import_id,
                value=ConditionFactValue(
                    clinical_status=_coded_status(condition, "clinicalStatus"),
                    onset_date=onset_date,
                ),
                normalization=FactNormalization(date=_normalized_date(onset_date)),
            )
        )
    return facts


def _observation_facts(
    bundle: Mapping[str, Any],
    *,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
) -> list[PatientFact]:
    """Normalize only numeric Observations; textual result content stays in source."""
    facts: list[PatientFact] = []
    for observation in _resources_of_type(bundle, "Observation"):
        value_quantity = observation.get("valueQuantity")
        numeric_value = _quantity_value(value_quantity)
        # A non-numeric FHIR Observation is not equivalent to a numeric fact.
        if numeric_value is None:
            continue
        effective_date = _string_field(observation, "effectiveDateTime")
        facts.extend(
            _coded_resource_facts(
                observation,
                resource_type="Observation",
                kind="observation",
                patient_id=patient_id,
                patient_references=patient_references,
                patient_import_id=patient_import_id,
                value=ObservationFactValue(
                    numeric_value=numeric_value,
                    status=_string_field(observation, "status"),
                    effective_date=effective_date,
                    reference_ranges=_observation_reference_ranges(observation),
                ),
                unit=_string_field(value_quantity, "unit"),
                normalization=FactNormalization(
                    date=_normalized_date(effective_date),
                    quantity=_normalized_quantity(value_quantity),
                ),
            )
        )
    return facts


def _medication_facts(
    bundle: Mapping[str, Any],
    *,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
) -> list[PatientFact]:
    """Normalize codeable medication statements and requests without resolving refs."""
    facts: list[PatientFact] = []
    for resource_type, date_fields in (
        ("MedicationStatement", ("effectiveDateTime", "dateAsserted")),
        ("MedicationRequest", ("authoredOn",)),
    ):
        for medication in _resources_of_type(bundle, resource_type):
            effective_date = _first_string_field(medication, date_fields)
            facts.extend(
                _coded_resource_facts(
                    medication,
                    resource_type=resource_type,
                    kind="medication",
                    patient_id=patient_id,
                    patient_references=patient_references,
                    patient_import_id=patient_import_id,
                    value=MedicationFactValue(
                        status=_string_field(medication, "status"),
                        intent=_string_field(medication, "intent"),
                        effective_date=effective_date,
                    ),
                    code_field="medicationCodeableConcept",
                    normalization=FactNormalization(
                        date=_normalized_date(effective_date)
                    ),
                )
            )
    return facts


def _procedure_facts(
    bundle: Mapping[str, Any],
    *,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
) -> list[PatientFact]:
    """Normalize Procedures while retaining their explicitly performed time only."""
    facts: list[PatientFact] = []
    for procedure in _resources_of_type(bundle, "Procedure"):
        performed_date = _first_string_field(procedure, ("performedDateTime",))
        performed_period = procedure.get("performedPeriod")
        if performed_date is None and isinstance(performed_period, Mapping):
            performed_date = _string_field(performed_period, "start")
        facts.extend(
            _coded_resource_facts(
                procedure,
                resource_type="Procedure",
                kind="procedure",
                patient_id=patient_id,
                patient_references=patient_references,
                patient_import_id=patient_import_id,
                value=ProcedureFactValue(
                    status=_string_field(procedure, "status"),
                    performed_date=performed_date,
                ),
                normalization=FactNormalization(date=_normalized_date(performed_date)),
            )
        )
    return facts


def _allergy_facts(
    bundle: Mapping[str, Any],
    *,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
) -> list[PatientFact]:
    """Normalize AllergyIntolerance records with their original status evidence."""
    facts: list[PatientFact] = []
    for allergy in _resources_of_type(bundle, "AllergyIntolerance"):
        recorded_date = _string_field(allergy, "recordedDate")
        facts.extend(
            _coded_resource_facts(
                allergy,
                resource_type="AllergyIntolerance",
                kind="allergy",
                patient_id=patient_id,
                patient_references=patient_references,
                patient_import_id=patient_import_id,
                value=AllergyFactValue(
                    clinical_status=_coded_status(allergy, "clinicalStatus"),
                    verification_status=_coded_status(allergy, "verificationStatus"),
                    recorded_date=recorded_date,
                ),
                reference_field="patient",
                normalization=FactNormalization(date=_normalized_date(recorded_date)),
            )
        )
    return facts


def _resources_of_type(
    bundle: Mapping[str, Any], resource_type: str
) -> list[Mapping[str, Any]]:
    entries = bundle["entry"]
    return [
        resource
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance((resource := entry.get("resource")), Mapping)
        and resource.get("resourceType") == resource_type
    ]


def _coded_resource_facts(
    resource: Mapping[str, Any],
    *,
    resource_type: str,
    kind: PatientFactKind,
    patient_id: str,
    patient_references: frozenset[str],
    patient_import_id: UUID,
    value: Any,
    code_field: str = "code",
    reference_field: str = "subject",
    unit: str | None = None,
    normalization: FactNormalization | None = None,
) -> list[PatientFact]:
    """Create one fact per source coding with one resource-level provenance link."""
    if _patient_reference(resource, reference_field) not in patient_references:
        raise FHIRPatientNormalizationError(
            f"FHIR {resource_type} {reference_field} must reference the Bundle Patient."
        )

    resource_id = _resource_id(resource, resource_type=resource_type)
    provenance = FHIRProvenance(
        resource_type=resource_type,
        resource_id=resource_id,
        version_id=_resource_version_id(resource),
    )
    return [
        PatientFact(
            fact_id=_fact_id(
                patient_import_id,
                resource_id,
                f"coding-{coding_index}",
                resource_type=resource_type,
            ),
            patient_id=patient_id,
            kind=kind,
            code=code,
            value=value,
            unit=unit,
            effective_at=(
                normalization.date.normalized_at
                if normalization is not None and normalization.date is not None
                else None
            ),
            source=provenance,
            source_resource=_source_resource_copy(resource),
            normalization=normalization or FactNormalization(),
        )
        for coding_index, code in enumerate(
            _coded_concept_codes(resource.get(code_field), resource_type)
        )
    ]


def _patient_reference(resource: Mapping[str, Any], field_name: str) -> str | None:
    subject = resource.get(field_name)
    if not isinstance(subject, Mapping):
        return None
    reference = subject.get("reference")
    return reference if isinstance(reference, str) and reference.strip() else None


def _coded_concept_codes(code: Any, resource_type: str) -> list[ClinicalCode]:
    if not isinstance(code, Mapping) or not isinstance(code.get("coding"), list):
        raise FHIRPatientNormalizationError(
            f"FHIR {resource_type} must contain at least one coded clinical concept."
        )

    fallback_display = code.get("text") if isinstance(code.get("text"), str) else None
    clinical_codes: list[ClinicalCode] = []
    for coding in code["coding"]:
        if not isinstance(coding, Mapping):
            continue
        system = coding.get("system")
        value = coding.get("code")
        if not isinstance(system, str) or not system.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        display = coding.get("display")
        clinical_codes.append(
            ClinicalCode(
                system=system,
                value=value,
                display=(
                    display
                    if isinstance(display, str) and display.strip()
                    else fallback_display
                ),
            )
        )
    if not clinical_codes:
        raise FHIRPatientNormalizationError(
            f"FHIR {resource_type} must contain a coding with system and code."
        )
    return clinical_codes


def _coded_status(resource: Mapping[str, Any], field_name: str) -> str | None:
    status = resource.get(field_name)
    if not isinstance(status, Mapping):
        return None
    codings = status.get("coding")
    if not isinstance(codings, list):
        return None
    for coding in codings:
        if (
            isinstance(coding, Mapping)
            and isinstance(coding.get("code"), str)
            and coding["code"].strip()
        ):
            return coding["code"]
    return None


def _string_field(resource: Mapping[str, Any] | Any, field_name: str) -> str | None:
    if not isinstance(resource, Mapping):
        return None
    value = resource.get(field_name)
    return value if isinstance(value, str) and value.strip() else None


def _first_string_field(
    resource: Mapping[str, Any], field_names: tuple[str, ...]
) -> str | None:
    return next(
        (
            value
            for field_name in field_names
            if (value := _string_field(resource, field_name)) is not None
        ),
        None,
    )


def _quantity_value(quantity: Any) -> int | float | None:
    if not isinstance(quantity, Mapping):
        return None
    value = quantity.get("value")
    # bool is an int subclass but never a valid numeric clinical measurement.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _observation_reference_ranges(
    observation: Mapping[str, Any],
) -> list[ObservationReferenceRange]:
    ranges = observation.get("referenceRange")
    if not isinstance(ranges, list):
        return []

    normalized_ranges: list[ObservationReferenceRange] = []
    for reference_range in ranges:
        if not isinstance(reference_range, Mapping):
            continue
        low = _reference_range_quantity(reference_range.get("low"))
        high = _reference_range_quantity(reference_range.get("high"))
        text = _string_field(reference_range, "text")
        if low is not None or high is not None or text is not None:
            normalized_ranges.append(
                ObservationReferenceRange(low=low, high=high, text=text)
            )
    return normalized_ranges


def _reference_range_quantity(quantity: Any) -> ReferenceRangeQuantity | None:
    value = _quantity_value(quantity)
    if value is None:
        return None
    return ReferenceRangeQuantity(value=value, unit=_string_field(quantity, "unit"))


def _normalized_date(source_value: str | None) -> DateNormalization | None:
    """Parse FHIR date precision without replacing unknown components with guesses."""
    if source_value is None:
        return None
    try:
        if _FHIR_YEAR_PATTERN.fullmatch(source_value):
            return DateNormalization(source_value=source_value, precision="year")
        if _FHIR_MONTH_PATTERN.fullmatch(source_value):
            year, month = (int(part) for part in source_value.split("-"))
            date(year, month, 1)
            return DateNormalization(source_value=source_value, precision="month")
        if "T" not in source_value:
            return DateNormalization(
                source_value=source_value,
                precision="day",
                normalized_date=date.fromisoformat(source_value),
            )
        parsed = datetime.fromisoformat(source_value.replace("Z", "+00:00"))
    except ValueError:
        return None

    return DateNormalization(
        source_value=source_value,
        precision="datetime",
        normalized_date=parsed.date(),
        normalized_at=(parsed.astimezone(UTC) if parsed.tzinfo is not None else None),
    )


def _normalized_quantity(quantity: Any) -> QuantityNormalization | None:
    value = _quantity_value(quantity)
    if value is None:
        return None
    return QuantityNormalization(
        value=value,
        unit=_string_field(quantity, "unit"),
        system=_string_field(quantity, "system"),
        code=_string_field(quantity, "code"),
    )


def _source_resource_copy(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy JSON FHIR so future callers cannot mutate a fact's evidence."""
    return json.loads(json.dumps(resource))


def _patient_demographic_issues(
    patient_resource: Mapping[str, Any], provenance: FHIRProvenance
) -> list[ImportDataQualityIssue]:
    """Record absent demographics as unknown evidence, never as a negative value."""
    issues: list[ImportDataQualityIssue] = []
    for field_name in ("gender", "birthDate"):
        if _string_field(patient_resource, field_name) is None:
            issues.append(
                ImportDataQualityIssue(
                    code="missing",
                    field=field_name,
                    message=f"Patient {field_name} is absent from the source resource.",
                    source=provenance,
                )
            )
    return issues


def _observation_value_issues(
    bundle: Mapping[str, Any], patient_references: frozenset[str]
) -> list[ImportDataQualityIssue]:
    """Make skipped non-numeric Observations visible rather than dropping them."""
    issues: list[ImportDataQualityIssue] = []
    for observation in _resources_of_type(bundle, "Observation"):
        if _patient_reference(observation, "subject") not in patient_references:
            continue
        if _quantity_value(observation.get("valueQuantity")) is not None:
            continue
        resource_id = _resource_id(observation, resource_type="Observation")
        issues.append(
            ImportDataQualityIssue(
                code=(
                    "missing" if observation.get("valueQuantity") is None else "invalid"
                ),
                field="valueQuantity.value",
                message="Observation lacks a usable numeric valueQuantity.value.",
                source=FHIRProvenance(
                    resource_type="Observation",
                    resource_id=resource_id,
                    version_id=_resource_version_id(observation),
                ),
            )
        )
    return issues


def _annotate_fact_quality(
    facts: list[PatientFact], *, evaluated_at: datetime
) -> list[PatientFact]:
    """Mark uncertainty on every fact while retaining all source facts as evidence."""
    quality_by_fact_id: dict[str, list[DataQualityIssue]] = {
        fact.fact_id: _base_quality_issues(fact, evaluated_at=evaluated_at)
        for fact in facts
    }
    for fact_id in _conflicting_observation_fact_ids(facts):
        quality_by_fact_id[fact_id].append(
            DataQualityIssue(
                code="conflicting",
                field="valueQuantity.value",
                message=(
                    "Another Observation has the same code and effective date "
                    "with a different numeric value."
                ),
            )
        )
    return [
        fact.model_copy(update={"quality_issues": quality_by_fact_id[fact.fact_id]})
        for fact in facts
    ]


def _base_quality_issues(
    fact: PatientFact, *, evaluated_at: datetime
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    date_normalization = fact.normalization.date
    if fact.kind in {"condition", "observation", "medication", "procedure", "allergy"}:
        if date_normalization is None:
            issues.append(
                DataQualityIssue(
                    code=(
                        "invalid"
                        if _source_date_value(fact.source_resource) is not None
                        else "missing"
                    ),
                    field="date",
                    message=(
                        "The recorded source date is not valid FHIR date/dateTime text."
                        if _source_date_value(fact.source_resource) is not None
                        else "The source resource has no usable recorded date."
                    ),
                )
            )
        elif date_normalization.normalized_date <= (
            evaluated_at.date() - _STALE_FACT_AGE
        ):
            issues.append(
                DataQualityIssue(
                    code="stale",
                    field="date",
                    message=(
                        "The source date is more than 365 days before "
                        "import evaluation."
                    ),
                )
            )
    if fact.kind == "observation":
        if fact.unit is None:
            issues.append(
                DataQualityIssue(
                    code="missing",
                    field="valueQuantity.unit",
                    message="The numeric Observation has no recorded unit.",
                )
            )
        if isinstance(fact.value, ObservationFactValue) and fact.value.status is None:
            issues.append(
                DataQualityIssue(
                    code="missing",
                    field="status",
                    message="The numeric Observation has no recorded status.",
                )
            )
    return issues


def _source_date_value(resource: Mapping[str, Any]) -> str | None:
    """Find the FHIR date field used by the resource's normalized fact type."""
    resource_type = resource.get("resourceType")
    if resource_type == "Condition":
        return _string_field(resource, "onsetDateTime")
    if resource_type == "Observation":
        return _string_field(resource, "effectiveDateTime")
    if resource_type == "MedicationStatement":
        return _first_string_field(resource, ("effectiveDateTime", "dateAsserted"))
    if resource_type == "MedicationRequest":
        return _string_field(resource, "authoredOn")
    if resource_type == "Procedure":
        performed_period = resource.get("performedPeriod")
        return _string_field(resource, "performedDateTime") or _string_field(
            performed_period, "start"
        )
    if resource_type == "AllergyIntolerance":
        return _string_field(resource, "recordedDate")
    return None


def _conflicting_observation_fact_ids(facts: list[PatientFact]) -> set[str]:
    """Identify conflicting same-time values without ranking or replacing either one."""
    observations: dict[tuple[str, str, str, str], list[PatientFact]] = {}
    for fact in facts:
        date_normalization = fact.normalization.date
        if (
            fact.kind != "observation"
            or not isinstance(fact.value, ObservationFactValue)
            or date_normalization is None
        ):
            continue
        key = (
            fact.patient_id,
            fact.code.system,
            fact.code.value,
            date_normalization.source_value,
        )
        observations.setdefault(key, []).append(fact)

    return {
        fact.fact_id
        for observation_facts in observations.values()
        if len({fact.value.numeric_value for fact in observation_facts}) > 1
        for fact in observation_facts
    }


def _fact_quality_import_issues(
    facts: list[PatientFact],
) -> list[ImportDataQualityIssue]:
    """Expose fact-level uncertainty without returning raw clinical content."""
    return [
        ImportDataQualityIssue(
            code=issue.code,
            field=issue.field,
            message=issue.message,
            source=fact.source,
            fact_id=fact.fact_id,
        )
        for fact in facts
        for issue in fact.quality_issues
    ]


def _resource_id(resource: Mapping[str, Any], *, resource_type: str) -> str:
    resource_id = resource.get("id")
    if not isinstance(resource_id, str) or not _FHIR_ID_PATTERN.fullmatch(resource_id):
        raise FHIRPatientNormalizationError(
            f"FHIR {resource_type} resource must have a valid id."
        )
    return resource_id


def _resource_version_id(resource: Mapping[str, Any]) -> str | None:
    meta = resource.get("meta")
    if not isinstance(meta, Mapping):
        return None
    version_id = meta.get("versionId")
    return version_id if isinstance(version_id, str) and version_id.strip() else None


def _fact_id(
    patient_import_id: UUID,
    resource_id: str,
    property_name: str,
    *,
    resource_type: str = "Patient",
) -> str:
    """Make every normalized fact unique to its immutable import snapshot."""
    return f"{patient_import_id}:{resource_type}:{resource_id}:{property_name}"
