"""Persist immutable FHIR-import and ClinicalTrials.gov source snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Patient, PatientFactRecord, PatientImport, Trial, TrialVersion
from app.fhir.importer import FHIR_R4_VERSION, normalize_patient_resource
from app.fhir.safety import require_synthetic_fhir_bundle
from app.fhir.schemas import (
    FHIRImportRequest,
    PatientFactResponse,
    PatientFactValue,
    PatientImportSnapshotResponse,
    PatientTimelineResponse,
)

_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class TrialSnapshotError(ValueError):
    """Raised when a caller provides an invalid trial snapshot identity or timestamp."""


@dataclass(frozen=True, slots=True)
class PatientImportResult:
    """Operational identifiers returned after one atomic synthetic FHIR import."""

    patient_id: str
    patient_import_id: UUID
    fact_ids: tuple[str, ...]
    data_quality_issues: tuple[dict[str, Any], ...]


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
        data_quality_issues=tuple(
            issue.model_dump(mode="json")
            for issue in normalized_patient.data_quality_issues
        ),
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
            data_quality_issues=patient_import.data_quality,
        ),
        facts=[
            PatientFactResponse(
                fact_id=fact.id,
                kind=fact.kind,
                code=fact.code,
                value=fact.value,
                unit=fact.unit,
                effective_at=fact.effective_at,
                source=fact.provenance,
                source_resource=fact.source_resource,
                normalization=fact.normalization,
                quality_issues=fact.quality_issues,
            )
            for fact in facts
        ],
    )


def _fact_value_for_storage(value: PatientFactValue | None) -> Any | None:
    """Serialize structured fact values for JSONB without changing scalar values."""
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


async def store_trial_version(
    session: AsyncSession,
    *,
    nct_id: str,
    raw_study: Mapping[str, Any],
    source_updated_at: datetime | None = None,
) -> TrialVersion:
    """Store a new immutable snapshot and update only the mutable projection."""
    if not _NCT_ID_PATTERN.fullmatch(nct_id):
        raise TrialSnapshotError("Trial snapshot requires an NCT identifier.")
    if source_updated_at is not None and source_updated_at.tzinfo is None:
        raise TrialSnapshotError("Trial source update time must be timezone-aware.")

    snapshot, source_hash = canonical_json_snapshot(raw_study)
    trial = await session.get(Trial, nct_id)
    if trial is None:
        session.add(
            Trial(
                nct_id=nct_id,
                current_data=snapshot,
                source_updated_at=source_updated_at,
            )
        )
        # A source version must never race ahead of its parent trial projection.
        await session.flush()
    else:
        # Only this projection changes; each source snapshot remains immutable.
        trial.current_data = snapshot
        trial.source_updated_at = source_updated_at

    trial_version = TrialVersion(
        id=uuid4(),
        nct_id=nct_id,
        source_hash=source_hash,
        raw_study=snapshot,
        source_updated_at=source_updated_at,
    )
    session.add(trial_version)
    await session.flush()
    return trial_version
