"""PostgreSQL checks for queued lexical match runs and persisted rankings."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.models import PatientFactRecord, Trial, TrialMatch
from src.fhir.safety import synthetic_data_tag
from src.fhir.schemas import FHIRImportRequest
from src.retrieval.semantic import SemanticTrialCandidate
from src.services.match_runs import (
    MAX_MATCH_RUN_CANDIDATES,
    cancel_match_run,
    create_queued_match_run,
    match_run_response,
    match_run_results,
)
from src.services.source_snapshots import (
    persist_synthetic_patient_import,
    store_trial_version,
)
from src.workers import match_runs as match_run_worker
from src.workers.match_runs import run_match_run_job

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
def test_worker_persists_ranked_trial_versions_for_one_immutable_patient_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker stores ranks and score components without returning patient text."""

    async def no_semantic(
        *_: object, **__: object
    ) -> tuple[SemanticTrialCandidate, ...]:
        return ()

    monkeypatch.setattr(match_run_worker, "semantic_trial_candidates", no_semantic)

    async def check(session: AsyncSession) -> None:
        patient_import = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=_synthetic_diabetes_bundle())
        )
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        trial_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {
                        "nctId": nct_id,
                        "briefTitle": "Diabetes and metformin study",
                    },
                    "conditionsModule": {"conditions": ["Diabetes mellitus"]},
                    "armsInterventionsModule": {
                        "interventions": [{"name": "Metformin", "type": "DRUG"}]
                    },
                    "eligibilityModule": {
                        "eligibilityCriteria": "Adults with diabetes"
                    },
                    "statusModule": {"overallStatus": "RECRUITING"},
                }
            },
            retrieved_at=datetime(2026, 8, 23, tzinfo=UTC),
            source_updated_at=datetime(2026, 8, 22, tzinfo=UTC),
        )
        run = await create_queued_match_run(
            session, patient_import_id=patient_import.patient_import_id
        )

        completed_run = await run_match_run_job(session, run.id)
        matches = list(
            await session.scalars(
                select(TrialMatch)
                .where(TrialMatch.match_run_id == run.id)
                .order_by(TrialMatch.candidate_rank)
            )
        )
        assert run.configuration_snapshot["candidate_limit"] == MAX_MATCH_RUN_CANDIDATES
        assert run.configuration_snapshot["patient_import_id"] == str(
            patient_import.patient_import_id
        )
        assert (
            run.configuration_snapshot["versions"]["rule_engine"] == "deterministic-v1"
        )
        assert run.configuration_snapshot["rank_fusion"] == {
            "method": "reciprocal-rank-fusion-v1",
            "rank_constant": 60,
        }
        assert run.rule_engine_version == "deterministic-v1"

        assert completed_run.status == "completed"
        assert len(matches) == 1
        assert matches[0].trial_version_id == trial_version.id
        assert matches[0].candidate_rank == 1
        assert matches[0].retrieval_scores["lexical_score"] > 0
        assert matches[0].retrieval_scores["matched_term_count"] >= 1

        response = await match_run_response(session, completed_run)
        results = await match_run_results(session, completed_run)
        assert response.candidate_limit == MAX_MATCH_RUN_CANDIDATES
        assert response.candidate_count == 1
        assert response.configuration_versions["retrieval"] == (
            "lexical-semantic-rrf-structured-reranker-v1"
        )
        assert results[0].structured_relevance is not None
        assert results[0].structured_relevance.status == "direct_support"
        assert results[0].structured_relevance.supported_fields == ["conditions"]
        assert results[0].nct_id == nct_id
        assert results[0].title == "Diabetes and metformin study"
        assert results[0].study_status == "RECRUITING"
        assert results[0].source_updated_at == datetime(2026, 8, 22, tzinfo=UTC)
        assert results[0].retrieval_relevance is not None
        assert results[0].retrieval_relevance.score > 0
        fact_ids = {
            record.id
            for record in await session.scalars(
                select(PatientFactRecord).where(
                    PatientFactRecord.patient_import_id
                    == patient_import.patient_import_id
                )
            )
        }
        assert results[0].trial_version_id == trial_version.id
        evidence_fact_ids = results[0].retrieval_scores["matched_fact_ids"]
        assert evidence_fact_ids
        assert set(evidence_fact_ids) <= fact_ids

        rerun = await create_queued_match_run(
            session, patient_import_id=patient_import.patient_import_id
        )
        completed_rerun = await run_match_run_job(session, rerun.id)
        rerun_results = await match_run_results(session, completed_rerun)

        assert rerun.id != run.id
        assert completed_rerun.status == "completed"
        assert rerun_results[0].id != results[0].id

    _run_database_check(check)


@pytest.mark.integration
def test_worker_uses_semantic_only_candidate_when_lexical_has_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic recall supplements rather than changes lexical candidate ordering."""

    async def check(session: AsyncSession) -> None:
        patient_import = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=_synthetic_diabetes_bundle())
        )
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        trial_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {
                        "nctId": nct_id,
                        "briefTitle": "A public study with different wording",
                    },
                    "eligibilityModule": {"eligibilityCriteria": "Adults only"},
                    "statusModule": {"overallStatus": "RECRUITING"},
                }
            },
            retrieved_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        trial = await session.get(Trial, nct_id)
        assert trial is not None

        async def semantic_only(
            *_: object, **__: object
        ) -> tuple[SemanticTrialCandidate, ...]:
            return (SemanticTrialCandidate(trial=trial, score=0.74, rank=1),)

        monkeypatch.setattr(
            match_run_worker, "semantic_trial_candidates", semantic_only
        )
        run = await create_queued_match_run(
            session, patient_import_id=patient_import.patient_import_id
        )
        completed = await run_match_run_job(session, run.id)
        match = await session.scalar(
            select(TrialMatch).where(TrialMatch.match_run_id == run.id)
        )

        assert completed.status == "completed"
        assert match is not None
        assert match.trial_version_id == trial_version.id
        assert match.retrieval_scores == {
            "candidate_sources": ["semantic"],
            "semantic_score": 0.74,
            "semantic_rank": 1,
            "reciprocal_rank_fusion_score": 1 / 61,
            "reciprocal_rank_fusion_rank": 1,
            "reciprocal_rank_fusion_rank_constant": 60,
            "reciprocal_rank_fusion_version": "reciprocal-rank-fusion-v1",
            "structured_evidence_reranker_version": "structured-evidence-reranker-v2",
            "structured_evidence_reranker_input_rank": 1,
            "structured_evidence_reranker_rank": 1,
            "structured_evidence_support_tier": 0,
            "structured_evidence_supported_fields": [],
            "structured_evidence_supporting_fact_ids": [],
            "structured_evidence_status": "unknown",
            "structured_evidence_note": (
                "No direct structured support was found; retained for review "
                "without a penalty."
            ),
        }
        results = await match_run_results(session, completed)
        assert results[0].retrieval_sources == ["semantic"]
        assert results[0].retrieval_relevance is None
        assert results[0].semantic_relevance is not None
        assert results[0].semantic_relevance.score == 0.74
        assert results[0].structured_relevance is not None
        assert results[0].structured_relevance.status == "unknown"

    _run_database_check(check)


@pytest.mark.integration
def test_cancelled_match_run_keeps_its_input_snapshot_without_creating_matches() -> (
    None
):
    """Cancellation is terminal and does not remove the immutable run evidence."""

    async def check(session: AsyncSession) -> None:
        patient_import = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=_synthetic_diabetes_bundle())
        )
        run = await create_queued_match_run(
            session, patient_import_id=patient_import.patient_import_id
        )
        cancelled_run = await cancel_match_run(session, run.id)
        worker_result = await run_match_run_job(session, run.id)

        assert cancelled_run.status == "cancelled"
        assert worker_result.status == "cancelled"
        response = await match_run_response(session, cancelled_run)
        assert response.candidate_count == 0
        assert response.completed_at is not None

    _run_database_check(check)


@pytest.mark.integration
def test_worker_persists_safe_failure_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected worker errors become static terminal status details."""

    async def fail(*_: object) -> None:
        raise RuntimeError("unexpected worker failure")

    monkeypatch.setattr(match_run_worker, "_persist_ranked_candidates", fail)

    async def check(session: AsyncSession) -> None:
        patient_import = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=_synthetic_diabetes_bundle())
        )
        run = await create_queued_match_run(
            session, patient_import_id=patient_import.patient_import_id
        )
        failed_run = await run_match_run_job(session, run.id)
        response = await match_run_response(session, failed_run)

        assert failed_run.status == "failed"
        assert response.failure is not None
        assert response.failure.code == "match_run.unexpected_error"
        assert response.failure.message == "Match run could not be completed."

    _run_database_check(check)


def _synthetic_diabetes_bundle() -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "meta": {"tag": [synthetic_data_tag()]},
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "match-run-patient",
                    "birthDate": "1980-08-23",
                    "gender": "female",
                }
            },
            {
                "resource": {
                    "onsetDateTime": "2026-08-01",
                    "resourceType": "Condition",
                    "id": "match-run-condition",
                    "subject": {"reference": "Patient/match-run-patient"},
                    "clinicalStatus": {
                        "coding": [
                            {
                                "code": "active",
                                "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            }
                        ]
                    },
                    "code": {
                        "coding": [
                            {
                                "system": "http://snomed.info/sct",
                                "code": "44054006",
                                "display": "Diabetes mellitus",
                            }
                        ]
                    },
                }
            },
        ],
    }
