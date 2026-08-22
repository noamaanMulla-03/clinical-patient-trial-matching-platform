"""PostgreSQL checks for source-linked import and trial snapshot persistence."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Patient, PatientFactRecord, PatientImport, Trial, TrialVersion
from app.fhir.safety import synthetic_data_tag
from app.fhir.schemas import FHIRImportRequest
from app.services.source_snapshots import (
    persist_synthetic_patient_import,
    store_trial_version,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DatabaseCheck = Callable[[AsyncSession], Awaitable[None]]


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
        patient_id = f"patient-{uuid4()}"
        request = FHIRImportRequest(
            bundle={
                "resourceType": "Bundle",
                "meta": {"tag": [synthetic_data_tag()]},
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Patient",
                            "id": patient_id,
                            "gender": "female",
                            "birthDate": "1990-05-01",
                            "meta": {"versionId": "1"},
                        }
                    }
                ],
            }
        )

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
        assert len(facts) == 2
        assert all(fact.created_at is not None for fact in facts)
        assert all(fact.provenance["resource_type"] == "Patient" for fact in facts)
        assert all(fact.provenance["resource_id"] == patient_id for fact in facts)
        assert all(fact.provenance["version_id"] == "1" for fact in facts)

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
