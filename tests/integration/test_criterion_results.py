"""PostgreSQL checks for criterion-result evidence and conservative aggregation."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.criteria.aggregation import aggregate_trial_match
from app.criteria.manual import create_manual_criteria
from app.criteria.results import CriterionResultError, store_criterion_result
from app.criteria.schemas import AtomicCriterion, CriterionEvaluation
from app.db.models import MatchRun, TrialMatch
from app.fhir.schemas import FHIRImportRequest
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
def test_results_require_snapshot_evidence_and_aggregate_to_a_bounded_outcome() -> None:
    """Every non-unknown result remains traceable to the selected import snapshot."""

    async def check(session: AsyncSession) -> None:
        bundle = json.loads(
            (FIXTURE_DIRECTORY / "synthea-r4-patient-02.json").read_text(
                encoding="utf-8"
            )
        )
        patient_import = await persist_synthetic_patient_import(
            session, FHIRImportRequest(bundle=bundle)
        )
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        eligibility_text = "Adults only"
        trial_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {"nctId": nct_id},
                    "eligibilityModule": {"eligibilityCriteria": eligibility_text},
                }
            },
            retrieved_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
        (criterion,) = await create_manual_criteria(
            session,
            trial_version_id=trial_version.id,
            criteria=[
                AtomicCriterion(
                    category="inclusion",
                    source_text="Adults",
                    source_start=0,
                    source_end=6,
                    rule={"kind": "age", "operator": "at_least", "years": 18},
                )
            ],
            parser_version="manual-fixture-v1",
        )
        match_run = MatchRun(
            id=uuid4(),
            patient_import_id=patient_import.patient_import_id,
            configuration_snapshot={},
            parser_version="fixture-v1",
            retrieval_version="fixture-v1",
            rule_engine_version="fixture-v1",
            terminology_mapping_version="fixture-v1",
            prompt_version="fixture-v1",
            model_configuration_version="fixture-v1",
            status="running",
        )
        trial_match = TrialMatch(
            id=uuid4(),
            match_run_id=match_run.id,
            trial_version_id=trial_version.id,
            candidate_rank=1,
            retrieval_scores={},
        )
        session.add_all([match_run, trial_match])
        await session.flush()

        result = await store_criterion_result(
            session,
            trial_match=trial_match,
            criterion=criterion,
            match_run=match_run,
            evaluation=CriterionEvaluation(
                outcome="met",
                evidence_fact_ids=[patient_import.fact_ids[0]],
                reason="predicate_matched",
                requires_review=False,
            ),
            evaluator_version="deterministic-test-v1",
        )
        assert result.evidence_fact_ids == [patient_import.fact_ids[0]]
        assert result.evaluator_version == "deterministic-test-v1"
        assert result.criterion_id == criterion.id
        assert result.explanation == "predicate_matched"

        with pytest.raises(
            CriterionResultError, match="missing from this patient import"
        ):
            await store_criterion_result(
                session,
                trial_match=trial_match,
                criterion=criterion,
                match_run=match_run,
                evaluation=CriterionEvaluation(
                    outcome="met",
                    evidence_fact_ids=["not-a-persisted-fact"],
                    reason="predicate_matched",
                    requires_review=False,
                ),
            )

        assert (
            await aggregate_trial_match(session, trial_match_id=trial_match.id)
        ).outcome == "potential_match"

    _run_database_check(check)
