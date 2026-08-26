"""Persist immutable FHIR-import and ClinicalTrials.gov source snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Patient, PatientFactRecord, PatientImport, Trial, TrialVersion
from src.fhir.importer import FHIR_R4_VERSION, normalize_patient_resource
from src.fhir.safety import require_synthetic_fhir_bundle
from src.fhir.schemas import (
    ClinicalCode,
    DataQualityIssue,
    FactNormalization,
    FHIRImportRequest,
    FHIRProvenance,
    ImportDataQualityIssue,
    PatientFactKind,
    PatientFactResponse,
    PatientFactSourceResponse,
    PatientFactValue,
    PatientImportSnapshotResponse,
    PatientTimelineResponse,
)
from src.services.trial_embeddings import queue_trial_embedding_job
from src.trials.extraction import ExtractedTrialFields, extract_trial_fields

_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class TrialSnapshotError(ValueError):
    """Raised when a caller provides an invalid trial snapshot identity or timestamp."""


@dataclass(frozen=True, slots=True)
class PatientImportResult:
    """Operational identifiers returned after one atomic synthetic FHIR import."""

    patient_id: str
    patient_import_id: UUID
    fact_ids: tuple[str, ...]
    data_quality_issues: tuple[ImportDataQualityIssue, ...]


def canonical_json_snapshot(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Deep-copy JSON source data and return its deterministic SHA-256 digest."""
    try:
        encoded = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Source snapshots must contain JSON-compatible values."
        ) from error
    return json.loads(encoded), hashlib.sha256(encoded.encode()).hexdigest()


async def persist_synthetic_patient_import(
    session: AsyncSession, request: FHIRImportRequest
) -> PatientImportResult:
    """Write a marked Bundle, stable patient identity, and normalized facts.

    The caller owns the transaction so the Bundle snapshot, PatientImport, and facts
    are committed or rolled back together.
    """
    # Recheck at the persistence boundary; Pydantic validation can be bypassed by code.
    require_synthetic_fhir_bundle(request.bundle)
    source_bundle, source_hash = canonical_json_snapshot(request.bundle)
    patient_import_id = uuid4()
    normalized_patient = normalize_patient_resource(
        source_bundle, patient_import_id=patient_import_id
    )
    patient = await session.get(Patient, normalized_patient.patient_id)
    if patient is None:
        session.add(
            Patient(
                id=normalized_patient.patient_id,
                external_ref=normalized_patient.patient_id,
                synthetic=True,
            )
        )

    session.add(
        PatientImport(
            id=patient_import_id,
            patient_id=normalized_patient.patient_id,
            fhir_version=FHIR_R4_VERSION,
            source_hash=source_hash,
            source_bundle=source_bundle,
            data_quality=[
                issue.model_dump(mode="json")
                for issue in normalized_patient.data_quality_issues
            ],
            status="completed",
            completed_at=datetime.now().astimezone(),
        )
    )
    # The models intentionally have no mutable ORM relationships; make the parent
    # snapshot visible before inserting its immutable fact records.
    await session.flush()
    for fact in normalized_patient.facts:
        session.add(
            PatientFactRecord(
                id=fact.fact_id,
                patient_id=fact.patient_id,
                patient_import_id=patient_import_id,
                kind=fact.kind,
                code=fact.code.model_dump(mode="json"),
                value=_fact_value_for_storage(fact.value),
                unit=fact.unit,
                effective_at=fact.effective_at,
                provenance=fact.source.model_dump(mode="json"),
                source_resource=fact.source_resource,
                normalization=fact.normalization.model_dump(mode="json"),
                quality_issues=[
                    issue.model_dump(mode="json") for issue in fact.quality_issues
                ],
            )
        )

    await session.flush()
    return PatientImportResult(
        patient_id=normalized_patient.patient_id,
        patient_import_id=patient_import_id,
        fact_ids=tuple(fact.fact_id for fact in normalized_patient.facts),
        data_quality_issues=normalized_patient.data_quality_issues,
    )


async def retrieve_patient_timeline(
    session: AsyncSession, patient_id: str
) -> PatientTimelineResponse | None:
    """Return one unmerged completed import so review evidence is never blended."""
    patient = await session.get(Patient, patient_id)
    if patient is None:
        return None

    patient_import = await session.scalar(
        select(PatientImport)
        .where(
            PatientImport.patient_id == patient_id,
            PatientImport.status == "completed",
        )
        .order_by(PatientImport.completed_at.desc(), PatientImport.created_at.desc())
        .limit(1)
    )
    if patient_import is None:
        return PatientTimelineResponse(
            patient_id=patient.id,
            synthetic=patient.synthetic,
            import_snapshot=None,
            facts=[],
        )

    facts = list(
        await session.scalars(
            select(PatientFactRecord)
            .where(PatientFactRecord.patient_import_id == patient_import.id)
            .order_by(
                PatientFactRecord.effective_at.desc().nulls_last(),
                PatientFactRecord.created_at.desc(),
                PatientFactRecord.id,
            )
        )
    )
    return PatientTimelineResponse(
        patient_id=patient.id,
        synthetic=patient.synthetic,
        import_snapshot=PatientImportSnapshotResponse(
            id=patient_import.id,
            fhir_version=patient_import.fhir_version,
            source_hash=patient_import.source_hash,
            created_at=patient_import.created_at,
            completed_at=patient_import.completed_at,
            data_quality_issues=[
                ImportDataQualityIssue.model_validate(issue)
                for issue in patient_import.data_quality
            ],
        ),
        facts=[
            PatientFactResponse(
                fact_id=fact.id,
                kind=cast(PatientFactKind, fact.kind),
                code=ClinicalCode.model_validate(fact.code),
                value=fact.value,
                unit=fact.unit,
                effective_at=fact.effective_at,
                source=FHIRProvenance.model_validate(fact.provenance),
                source_resource=fact.source_resource,
                normalization=FactNormalization.model_validate(fact.normalization),
                quality_issues=[
                    DataQualityIssue.model_validate(issue)
                    for issue in fact.quality_issues
                ],
            )
            for fact in facts
        ],
    )


async def retrieve_patient_fact_source(
    session: AsyncSession, patient_id: str, fact_id: str
) -> PatientFactSourceResponse | None:
    """Return source evidence only when it belongs to the current import timeline."""
    patient_import = await session.scalar(
        select(PatientImport)
        .where(
            PatientImport.patient_id == patient_id,
            PatientImport.status == "completed",
        )
        .order_by(PatientImport.completed_at.desc(), PatientImport.created_at.desc())
        .limit(1)
    )
    if patient_import is None:
        return None

    fact = await session.scalar(
        select(PatientFactRecord).where(
            PatientFactRecord.patient_import_id == patient_import.id,
            PatientFactRecord.id == fact_id,
        )
    )
    if fact is None:
        return None

    return PatientFactSourceResponse(
        patient_id=patient_id,
        fact_id=fact.id,
        source=FHIRProvenance.model_validate(fact.provenance),
        source_resource=fact.source_resource,
    )


def _fact_value_for_storage(value: PatientFactValue | None) -> Any | None:
    """Serialize structured fact values for JSONB without changing scalar values."""
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


async def store_trial_version(
    session: AsyncSession,
    *,
    nct_id: str,
    raw_study: Mapping[str, Any],
    retrieved_at: datetime,
    source_updated_at: datetime | None = None,
    extracted_fields: ExtractedTrialFields | None = None,
) -> TrialVersion:
    """Store a new immutable snapshot and update only the mutable projection."""
    if not _NCT_ID_PATTERN.fullmatch(nct_id):
        raise TrialSnapshotError("Trial snapshot requires an NCT identifier.")
    if source_updated_at is not None and source_updated_at.tzinfo is None:
        raise TrialSnapshotError("Trial source update time must be timezone-aware.")
    if retrieved_at.tzinfo is None:
        raise TrialSnapshotError("Trial retrieval time must be timezone-aware.")

    trial_fields = extract_trial_fields(raw_study)
    if extracted_fields is not None:
        if extracted_fields != trial_fields:
            raise TrialSnapshotError(
                "Trial extracted fields do not match the unmodified source study."
            )
        trial_fields = extracted_fields
    if trial_fields.nct_id != nct_id:
        raise TrialSnapshotError(
            "Trial source snapshot NCT identifier does not match its storage identity."
        )

    projection_values = _trial_projection_values(trial_fields)
    matching_source_hash = trial_matching_source_hash(trial_fields)
    snapshot, source_hash = canonical_json_snapshot(raw_study)
    matching_reused_from_version_id = await session.scalar(
        select(TrialVersion.id)
        .where(
            TrialVersion.nct_id == nct_id,
            TrialVersion.matching_source_hash == matching_source_hash,
            TrialVersion.requires_reparse.is_(True),
        )
        .order_by(TrialVersion.ingested_at.desc(), TrialVersion.id.desc())
        .limit(1)
    )
    requires_reparse = matching_reused_from_version_id is None
    trial = await session.get(Trial, nct_id)
    if trial is None:
        session.add(
            Trial(
                nct_id=nct_id,
                current_data=snapshot,
                **projection_values,
                matching_source_hash=matching_source_hash,
                source_updated_at=source_updated_at,
                retrieved_at=retrieved_at,
            )
        )
        # A source version must never race ahead of its parent trial projection.
        await session.flush()
    else:
        # Only this projection changes; each source snapshot remains immutable.
        trial.current_data = snapshot
        for field_name, value in projection_values.items():
            setattr(trial, field_name, value)
        trial.matching_source_hash = matching_source_hash
        trial.source_updated_at = source_updated_at
        trial.retrieved_at = retrieved_at

    trial_version = TrialVersion(
        id=uuid4(),
        nct_id=nct_id,
        source_hash=source_hash,
        matching_source_hash=matching_source_hash,
        matching_reused_from_version_id=matching_reused_from_version_id,
        requires_reparse=requires_reparse,
        raw_study=snapshot,
        source_updated_at=source_updated_at,
        retrieved_at=retrieved_at,
    )
    session.add(trial_version)
    await session.flush()
    previous_current_versions = list(
        await session.scalars(
            select(TrialVersion)
            .where(
                TrialVersion.nct_id == nct_id,
                TrialVersion.id != trial_version.id,
                TrialVersion.superseded_at.is_(None),
            )
            .with_for_update()
        )
    )
    # Raw source evidence remains untouched. This one-way lifecycle link states
    # which later immutable source snapshot replaced the formerly current record.
    for previous_version in previous_current_versions:
        previous_version.superseded_by_version_id = trial_version.id
        previous_version.superseded_at = retrieved_at
    await queue_trial_embedding_job(session, trial_version_id=trial_version.id)
    await session.flush()
    return trial_version


def _trial_projection_values(fields: ExtractedTrialFields) -> dict[str, Any]:
    """Serialize deterministic extracted fields for the mutable trial projection."""
    return {
        "title": fields.title,
        "conditions": fields.conditions,
        "interventions": [
            intervention.model_dump(mode="json")
            for intervention in fields.interventions
        ],
        "status": fields.status,
        "phases": fields.phases,
        "eligibility_text": fields.eligibility_text,
        "minimum_age": fields.minimum_age,
        "maximum_age": fields.maximum_age,
        "sex": fields.sex,
        "locations": [
            location.model_dump(mode="json") for location in fields.locations
        ],
    }


def trial_matching_source_hash(fields: ExtractedTrialFields) -> str:
    """Hash only fields that can affect trial retrieval or criterion evaluation."""
    # Keep raw source identity separate: changes in an unrelated registration field
    # still receive an immutable source snapshot but do not falsely invalidate work.
    _, matching_source_hash = canonical_json_snapshot(
        {
            "nct_id": fields.nct_id,
            "matching_fields": _trial_projection_values(fields),
        }
    )
    return matching_source_hash
