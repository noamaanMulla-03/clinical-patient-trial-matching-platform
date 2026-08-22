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

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Patient, PatientFactRecord, PatientImport, Trial, TrialVersion
from app.fhir.importer import FHIR_R4_VERSION, normalize_patient_resource
from app.fhir.safety import require_synthetic_fhir_bundle
from app.fhir.schemas import FHIRImportRequest

_NCT_ID_PATTERN = re.compile(r"NCT\d{8}")


class TrialSnapshotError(ValueError):
    """Raised when a caller provides an invalid trial snapshot identity or timestamp."""


@dataclass(frozen=True, slots=True)
class PatientImportResult:
    """Operational identifiers returned after one atomic synthetic FHIR import."""

    patient_id: str
    patient_import_id: UUID
    fact_ids: tuple[str, ...]


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
                value=fact.value,
                unit=fact.unit,
                effective_at=fact.effective_at,
                provenance=fact.source.model_dump(mode="json"),
            )
        )

    await session.flush()
    return PatientImportResult(
        patient_id=normalized_patient.patient_id,
        patient_import_id=patient_import_id,
        fact_ids=tuple(fact.fact_id for fact in normalized_patient.facts),
    )


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
