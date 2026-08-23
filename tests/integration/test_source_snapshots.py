"""PostgreSQL checks for source-linked import and trial snapshot persistence."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Patient, PatientFactRecord, PatientImport, Trial, TrialVersion
from app.fhir.schemas import FHIRImportRequest
from app.routes.patients import get_synthetic_patient_timeline
from app.services.source_snapshots import (
    persist_synthetic_patient_import,
    store_trial_version,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DatabaseCheck = Callable[[AsyncSession], Awaitable[None]]
FIXTURE_DIRECTORY = Path(__file__).resolve().parents[2] / "datasets" / "fhir-r4"


def _run_database_check(check: DatabaseCheck) -> None:
    if TEST_DATABASE_URL is None:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration checks.")
    asyncio.run(_with_rollback(check))


async def _with_rollback(check: DatabaseCheck) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                await check(session)
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_synthetic_import_persists_a_patient_import_and_provenance_facts() -> None:
    """A database flush proves all source-linked records can be created together."""

    async def check(session: AsyncSession) -> None:
        bundle = json.loads(
            (FIXTURE_DIRECTORY / "synthea-r4-patient-02.json").read_text(
                encoding="utf-8"
            )
        )
        patient_resource = next(
            entry["resource"]
            for entry in bundle["entry"]
            if entry.get("resource", {}).get("resourceType") == "Patient"
        )
        patient_id = patient_resource["id"]
        request = FHIRImportRequest(bundle=bundle)

        result = await persist_synthetic_patient_import(session, request)
        patient = await session.get(Patient, result.patient_id)
        patient_import = await session.get(PatientImport, result.patient_import_id)
        facts = list(
            await session.scalars(
                select(PatientFactRecord).where(
                    PatientFactRecord.patient_import_id == result.patient_import_id
                )
            )
        )

        assert patient is not None and patient.synthetic is True
        assert patient_import is not None and patient_import.status == "completed"
        assert patient_import.data_quality
        assert len(facts) > 2
        assert all(fact.created_at is not None for fact in facts)
        assert all(
            fact.source_resource["id"] == fact.provenance["resource_id"]
            for fact in facts
        )
        assert all(
            fact.source_resource["resourceType"] == fact.provenance["resource_type"]
            for fact in facts
        )
        assert all("date" in fact.normalization for fact in facts)
        assert all(isinstance(fact.quality_issues, list) for fact in facts)
        demographic_facts = [fact for fact in facts if fact.kind == "demographic"]
        assert all(
            fact.provenance["resource_type"] == "Patient" for fact in demographic_facts
        )
        assert all(
            fact.provenance["resource_id"] == patient_id for fact in demographic_facts
        )
        assert all(
            fact.provenance["version_id"]
            == patient_resource.get("meta", {}).get("versionId")
            for fact in demographic_facts
        )
        condition_facts = [fact for fact in facts if fact.kind == "condition"]
        assert condition_facts
        assert all(
            fact.provenance["resource_type"] == "Condition" for fact in condition_facts
        )
        assert all("clinical_status" in fact.value for fact in condition_facts)
        assert all("onset_date" in fact.value for fact in condition_facts)
        observation_facts = [fact for fact in facts if fact.kind == "observation"]
        assert observation_facts
        assert all("numeric_value" in fact.value for fact in observation_facts)
        assert all(fact.unit is not None for fact in observation_facts)
        medication_facts = [fact for fact in facts if fact.kind == "medication"]
        assert medication_facts
        assert all(
            fact.provenance["resource_type"] == "MedicationRequest"
            for fact in medication_facts
        )
        procedure_facts = [fact for fact in facts if fact.kind == "procedure"]
        assert procedure_facts
        assert all("performed_date" in fact.value for fact in procedure_facts)

    _run_database_check(check)


@pytest.mark.integration
def test_trial_snapshots_preserve_prior_versions_while_updating_current_state() -> None:
    """A newer study source creates a second history record, not an overwrite."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        first_study = {"id": nct_id, "briefTitle": "Original synthetic study"}
        second_study = {"id": nct_id, "briefTitle": "Updated synthetic study"}

        first_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=first_study,
            source_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        second_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=second_study,
            source_updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        trial = await session.get(Trial, nct_id)
        versions = list(
            await session.scalars(
                select(TrialVersion)
                .where(TrialVersion.nct_id == nct_id)
                .order_by(TrialVersion.ingested_at, TrialVersion.id)
            )
        )

        assert trial is not None and trial.current_data == second_study
        assert {version.id for version in versions} == {
            first_version.id,
            second_version.id,
        }
        assert {version.source_hash: version.raw_study for version in versions} == {
            first_version.source_hash: first_study,
            second_version.source_hash: second_study,
        }
        assert first_version.source_hash != second_version.source_hash

    _run_database_check(check)


@pytest.mark.integration
def test_patient_timeline_returns_one_source_linked_completed_import() -> None:
    """Timeline output keeps the selected import and its evidence chain explicit."""

    async def check(session: AsyncSession) -> None:
        bundle = json.loads(
            (FIXTURE_DIRECTORY / "synthea-r4-patient-02.json").read_text(
                encoding="utf-8"
            )
        )
        import_result = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=bundle)
        )

        timeline = await get_synthetic_patient_timeline(
            import_result.patient_id, session
        )

        assert timeline is not None
        assert timeline.patient_id == import_result.patient_id
        assert timeline.synthetic is True
        assert timeline.import_snapshot is not None
        assert timeline.import_snapshot.id == import_result.patient_import_id
        assert timeline.facts
        assert {fact.fact_id for fact in timeline.facts} == set(import_result.fact_ids)
        assert all(fact.code.system and fact.code.value for fact in timeline.facts)
        assert all(fact.normalization is not None for fact in timeline.facts)
        assert all(
            fact.source_resource["id"] == fact.source.resource_id
            and fact.source_resource["resourceType"] == fact.source.resource_type
            for fact in timeline.facts
        )

    _run_database_check(check)
