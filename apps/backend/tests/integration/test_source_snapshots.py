"""PostgreSQL checks for source-linked import and trial snapshot persistence."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.clients.clinicaltrials import (
    ClinicalTrialsRequestError,
    ClinicalTrialsStudiesPage,
    ClinicalTrialsStudyResponse,
)
from app.db.models import (
    Patient,
    PatientFactRecord,
    PatientImport,
    Trial,
    TrialSync,
    TrialVersion,
)
from app.errors import APIError
from app.fhir.schemas import FHIRImportRequest
from app.routes.patients import get_synthetic_patient_timeline
from app.routes.trial_syncs import (
    create_trial_sync,
    get_trial_catalogue_status,
    get_trial_sync,
    queue_fixed_development_trial_collection,
)
from app.services.source_snapshots import (
    persist_synthetic_patient_import,
    store_trial_version,
)
from app.trials.development_collection import (
    DEVELOPMENT_TRIAL_COLLECTION,
    queue_development_trial_collection,
)
from app.trials.schemas import TrialSyncCreateRequest
from app.workers.trial_ingestion import (
    TrialIngestionRequest,
    run_trial_ingestion_job,
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
        first_study = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": "Original synthetic study",
                }
            }
        }
        second_study = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": "Updated synthetic study",
                },
                "statusModule": {"overallStatus": "RECRUITING"},
                "conditionsModule": {"conditions": ["Melanoma"]},
                "designModule": {"phases": ["PHASE2"]},
                "armsInterventionsModule": {
                    "interventions": [{"name": "Synthetic drug", "type": "DRUG"}]
                },
                "eligibilityModule": {
                    "eligibilityCriteria": "Adults only",
                    "minimumAge": "18 Years",
                    "maximumAge": "75 Years",
                    "sex": "ALL",
                },
                "contactsLocationsModule": {
                    "locations": [{"city": "Boston", "country": "United States"}]
                },
            }
        }
        third_study = deepcopy(second_study)
        third_study["protocolSection"]["identificationModule"]["organization"] = {
            "fullName": "Updated sponsor metadata"
        }

        first_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=first_study,
            retrieved_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
            source_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        second_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=second_study,
            retrieved_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
            source_updated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        third_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=third_study,
            retrieved_at=datetime(2026, 8, 3, 1, tzinfo=UTC),
            source_updated_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
        trial = await session.get(Trial, nct_id)
        versions = list(
            await session.scalars(
                select(TrialVersion)
                .where(TrialVersion.nct_id == nct_id)
                .order_by(TrialVersion.ingested_at, TrialVersion.id)
            )
        )

        assert trial is not None and trial.current_data == third_study
        assert trial.title == "Updated synthetic study"
        assert trial.conditions == ["Melanoma"]
        assert trial.interventions == [
            {
                "name": "Synthetic drug",
                "type": "DRUG",
                "description": None,
                "other_names": [],
            }
        ]
        assert trial.status == "RECRUITING"
        assert trial.phases == ["PHASE2"]
        assert trial.eligibility_text == "Adults only"
        assert trial.minimum_age == "18 Years"
        assert trial.maximum_age == "75 Years"
        assert trial.sex == "ALL"
        assert trial.locations == [
            {
                "facility": None,
                "status": None,
                "city": "Boston",
                "state": None,
                "postal_code": None,
                "country": "United States",
            }
        ]
        assert trial.matching_source_hash == third_version.matching_source_hash
        assert first_version.matching_source_hash != second_version.matching_source_hash
        assert second_version.matching_source_hash == third_version.matching_source_hash
        assert first_version.requires_reparse is True
        assert second_version.requires_reparse is True
        assert third_version.requires_reparse is False
        assert third_version.matching_reused_from_version_id == second_version.id
        assert first_version.superseded_by_version_id == second_version.id
        assert first_version.superseded_at == datetime(2026, 8, 2, 1, tzinfo=UTC)
        assert second_version.superseded_by_version_id == third_version.id
        assert second_version.superseded_at == datetime(2026, 8, 3, 1, tzinfo=UTC)
        assert third_version.superseded_by_version_id is None
        assert third_version.superseded_at is None
        assert {version.id for version in versions} == {
            first_version.id,
            second_version.id,
            third_version.id,
        }
        assert {version.source_hash: version.raw_study for version in versions} == {
            first_version.source_hash: first_study,
            second_version.source_hash: second_study,
            third_version.source_hash: third_study,
        }
        assert len({version.source_hash for version in versions}) == 3
        assert trial.retrieved_at == datetime(2026, 8, 3, 1, tzinfo=UTC)
        assert {version.retrieved_at for version in versions} == {
            datetime(2026, 8, 1, 1, tzinfo=UTC),
            datetime(2026, 8, 2, 1, tzinfo=UTC),
            datetime(2026, 8, 3, 1, tzinfo=UTC),
        }

    _run_database_check(check)


@pytest.mark.integration
def test_trial_ingestion_stores_a_later_bounded_search_page_without_rewriting_it() -> (
    None
):
    """Opaque cursors are walked safely, while only the requested range is stored."""

    class FakeTrialClient:
        def __init__(
            self,
            pages: list[ClinicalTrialsStudiesPage],
            study_by_nct_id: dict[str, dict[str, object]],
        ) -> None:
            self.pages = pages
            self.study_by_nct_id = study_by_nct_id
            self.search_calls: list[dict[str, object]] = []

        async def get_study(self, nct_id: str) -> ClinicalTrialsStudyResponse:
            return ClinicalTrialsStudyResponse(
                study=self.study_by_nct_id[nct_id],
                retrieved_at=datetime(2026, 8, 3, tzinfo=UTC),
            )

        async def search_studies(
            self,
            *,
            query_term: str | None = None,
            condition: str | None = None,
            page_size: int = 100,
            page_token: str | None = None,
        ) -> ClinicalTrialsStudiesPage:
            self.search_calls.append(
                {
                    "query_term": query_term,
                    "condition": condition,
                    "page_size": page_size,
                    "page_token": page_token,
                }
            )
            return self.pages[(len(self.search_calls) - 1) % len(self.pages)]

    async def check(session: AsyncSession) -> None:
        first_nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        second_nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        direct_nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        first_study = {
            "protocolSection": {"identificationModule": {"nctId": first_nct_id}}
        }
        second_study = {
            "protocolSection": {
                "identificationModule": {"nctId": second_nct_id},
                "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-08-01"}},
            }
        }
        direct_study = {
            "protocolSection": {"identificationModule": {"nctId": direct_nct_id}}
        }
        client = FakeTrialClient(
            [
                ClinicalTrialsStudiesPage(
                    studies=(first_study,),
                    next_page_token="second-page",
                    retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
                ),
                ClinicalTrialsStudiesPage(
                    studies=(second_study,),
                    next_page_token=None,
                    retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
                ),
            ],
            {direct_nct_id: direct_study},
        )
        request = TrialIngestionRequest(
            query_term="cancer",
            condition="melanoma",
            start_page=2,
            end_page=2,
            page_size=5,
        )

        result = await run_trial_ingestion_job(session, request, client=client)
        client.pages[1] = ClinicalTrialsStudiesPage(
            studies=(second_study,),
            next_page_token=None,
            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
        repeated_result = await run_trial_ingestion_job(session, request, client=client)
        source_metadata_change = deepcopy(second_study)
        source_metadata_change["protocolSection"]["identificationModule"][
            "organization"
        ] = {"fullName": "Updated sponsor metadata"}
        client.pages[1] = ClinicalTrialsStudiesPage(
            studies=(source_metadata_change,),
            next_page_token=None,
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        )
        reused_result = await run_trial_ingestion_job(session, request, client=client)
        direct_result = await run_trial_ingestion_job(
            session,
            TrialIngestionRequest(nct_id=direct_nct_id),
            client=client,
        )
        versions = list(
            await session.scalars(
                select(TrialVersion).where(TrialVersion.nct_id == second_nct_id)
            )
        )
        second_trial = await session.get(Trial, second_nct_id)

        assert client.search_calls == [
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": None,
            },
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": "second-page",
            },
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": None,
            },
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": "second-page",
            },
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": None,
            },
            {
                "query_term": "cancer",
                "condition": "melanoma",
                "page_size": 5,
                "page_token": "second-page",
            },
        ]
        assert result.pages_fetched == 2
        assert result.studies_processed == 1
        assert result.versions_created == 1
        assert result.unchanged_studies == 0
        assert result.versions_requiring_reparse == 1
        assert result.versions_reusing_matching_results == 0
        assert result.source_records_with_update_time == 1
        assert result.source_records_missing_update_time == 0
        assert result.source_records_invalid_update_time == 0
        assert result.max_source_lag_seconds == 86_400
        assert repeated_result.versions_created == 0
        assert repeated_result.unchanged_studies == 1
        assert repeated_result.versions_requiring_reparse == 0
        assert repeated_result.versions_reusing_matching_results == 0
        assert repeated_result.source_records_with_update_time == 1
        assert reused_result.versions_created == 1
        assert reused_result.versions_requiring_reparse == 0
        assert reused_result.versions_reusing_matching_results == 1
        assert reused_result.source_records_with_update_time == 1
        assert direct_result.pages_fetched == 0
        assert direct_result.studies_processed == 1
        assert direct_result.versions_created == 1
        assert direct_result.versions_requiring_reparse == 1
        assert direct_result.source_records_missing_update_time == 1
        assert result.status == "completed"
        sync = await session.get(TrialSync, result.sync_id)
        assert sync is not None and sync.status == "completed"
        assert sync.max_source_lag_seconds == 86_400
        assert await session.get(Trial, first_nct_id) is None
        assert len(versions) == 2
        reused_version = next(
            version
            for version in versions
            if version.raw_study == source_metadata_change
        )
        original_version = next(
            version for version in versions if version.raw_study == second_study
        )
        assert reused_version.requires_reparse is False
        assert reused_version.matching_reused_from_version_id == original_version.id
        assert second_trial is not None
        assert second_trial.retrieved_at == datetime(2026, 8, 5, tzinfo=UTC)
        assert await session.get(Trial, direct_nct_id) is not None

    _run_database_check(check)


@pytest.mark.integration
def test_trial_ingestion_records_a_safe_failure_without_persisting_partial_work() -> (
    None
):
    """A remote failure produces a durable failed sync with no raw source writes."""

    class FailingTrialClient:
        async def get_study(self, nct_id: str) -> ClinicalTrialsStudyResponse:
            raise ClinicalTrialsRequestError(
                "remote source content must not be retained"
            )

        async def search_studies(
            self,
            *,
            query_term: str | None = None,
            condition: str | None = None,
            page_size: int = 100,
            page_token: str | None = None,
        ) -> ClinicalTrialsStudiesPage:
            raise AssertionError("unexpected search request")

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        result = await run_trial_ingestion_job(
            session,
            TrialIngestionRequest(nct_id=nct_id),
            client=FailingTrialClient(),
        )
        sync = await session.get(TrialSync, result.sync_id)

        assert result.status == "failed"
        assert result.failure_code == "remote_request_failed"
        assert result.failure_message == "ClinicalTrials.gov request failed."
        assert result.versions_created == 0
        assert sync is not None and sync.status == "failed"
        assert sync.failure_message == "ClinicalTrials.gov request failed."
        assert await session.get(Trial, nct_id) is None

    _run_database_check(check)


@pytest.mark.integration
def test_trial_sync_routes_persist_and_return_a_queued_bounded_job() -> None:
    """The API creates a durable job before any worker calls the source."""

    async def check(session: AsyncSession) -> None:
        created = await create_trial_sync(
            TrialSyncCreateRequest(query_term="  melanoma  ", page_size=5),
            session,
        )
        retrieved = await get_trial_sync(created.id, session)
        sync = await session.get(TrialSync, created.id)

        assert created.status == "queued"
        assert created.selection.mode == "search"
        assert created.selection.query_term == "melanoma"
        assert created.selection.start_page == 1
        assert created.selection.end_page == 1
        assert created.counts.studies_processed == 0
        assert created.failure is None
        assert retrieved == created
        assert sync is not None and sync.request_parameters == {
            "mode": "search",
            "collection_id": None,
            "nct_id": None,
            "query_term": "melanoma",
            "condition": None,
            "start_page": 1,
            "end_page": 1,
            "page_size": 5,
        }

        with pytest.raises(APIError) as error:
            await get_trial_sync(uuid4(), session)
        assert error.value.status_code == 404
        assert error.value.code == "trial_sync.not_found"

    _run_database_check(check)


@pytest.mark.integration
def test_development_collection_queues_the_fixed_source_annotated_jobs() -> None:
    """Development syncs retain fixed membership and their collection provenance."""

    async def check(session: AsyncSession) -> None:
        syncs = await queue_development_trial_collection(session)

        assert len(syncs) == len(DEVELOPMENT_TRIAL_COLLECTION.nct_ids)
        assert all(sync.status == "queued" for sync in syncs)
        assert [sync.request_parameters for sync in syncs] == [
            {
                "mode": "nct_id",
                "collection_id": DEVELOPMENT_TRIAL_COLLECTION.collection_id,
                "nct_id": nct_id,
                "query_term": None,
                "condition": None,
                "start_page": None,
                "end_page": None,
                "page_size": 100,
            }
            for nct_id in DEVELOPMENT_TRIAL_COLLECTION.nct_ids
        ]


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


@pytest.mark.integration
def test_catalogue_routes_expose_safe_aggregate_readiness_and_fixed_jobs() -> None:
    """Catalogue readiness remains aggregate-only while the demo membership is fixed."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        retrieved_at = datetime(2026, 8, 24, tzinfo=UTC)
        sync = await create_trial_sync(TrialSyncCreateRequest(nct_id=nct_id), session)
        queued_demo_syncs = await queue_fixed_development_trial_collection(session)
        await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {"nctId": nct_id},
                    "statusModule": {"lastUpdateSubmitDate": "2026-08-20"},
                }
            },
            retrieved_at=retrieved_at,
            source_updated_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
        await session.execute(
            update(TrialSync)
            .where(TrialSync.id == sync.id)
            .values(status="completed", completed_at=retrieved_at)
        )
        await session.flush()

        catalogue = await get_trial_catalogue_status(session)

        assert catalogue.searchable_trial_count >= 1
        assert catalogue.latest_successful_update_at == retrieved_at
        assert catalogue.freshness.records_with_source_update_time >= 1
        assert catalogue.freshness.records_missing_source_update_time >= 0
        assert catalogue.freshness.newest_source_update_at == datetime(
            2026, 8, 20, tzinfo=UTC
        )
        assert catalogue.latest_sync is not None
        assert catalogue.state == "updating"
        assert (
            catalogue.latest_sync.selection.collection_id
            == DEVELOPMENT_TRIAL_COLLECTION.collection_id
        )
        assert len(queued_demo_syncs) == len(DEVELOPMENT_TRIAL_COLLECTION.nct_ids)
        assert {queued.selection.nct_id for queued in queued_demo_syncs} == set(
            DEVELOPMENT_TRIAL_COLLECTION.nct_ids
        )
        assert all(
            queued.selection.collection_id == DEVELOPMENT_TRIAL_COLLECTION.collection_id
            for queued in queued_demo_syncs
        )

    _run_database_check(check)
