"""PostgreSQL checks for automatic source-linked eligibility parsing."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.criteria.eligibility_parser import ELIGIBILITY_PARSER_VERSION
from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.db.models import Criterion, TrialParserRun
from src.services.source_snapshots import store_trial_version

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
def test_new_trial_version_persists_review_required_exactly_spanned_criteria() -> None:
    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        eligibility_text = "Inclusion Criteria:\n- Age 18 years or older"
        version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {"nctId": nct_id},
                    "eligibilityModule": {"eligibilityCriteria": eligibility_text},
                }
            },
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        criteria = list(
            await session.scalars(
                select(Criterion)
                .where(Criterion.trial_version_id == version.id)
                .order_by(Criterion.source_start, Criterion.id)
            )
        )

        assert len(criteria) == 1
        criterion = criteria[0]
        assert criterion.category == "inclusion"
        assert criterion.parsed_data == {
            "kind": "age",
            "operator": "at_least",
            "years": 18,
        }
        assert criterion.parser_version == ELIGIBILITY_PARSER_VERSION
        assert criterion.parser_confidence == 1
        assert criterion.requires_human_review is True
        assert criterion.review_reasons == []
        parser_run = await session.scalar(
            select(TrialParserRun).where(TrialParserRun.trial_version_id == version.id)
        )
        assert parser_run is not None
        assert parser_run.parser_version == ELIGIBILITY_PARSER_VERSION
        assert parser_run.prompt_version == (
            ELIGIBILITY_PARSER_CONFIGURATION.prompt_version
        )
        assert parser_run.model_configuration_version == (
            ELIGIBILITY_PARSER_CONFIGURATION.model_configuration_version
        )
        assert parser_run.raw_output["criteria"] == [
            {
                "category": "inclusion",
                "source_text": "Age 18 years or older",
                "source_start": 22,
                "source_end": 43,
                "rule": {"kind": "age", "operator": "at_least", "years": 18},
                "parser_confidence": "1.0000",
                "review_reasons": [],
            }
        ]
        assert (
            eligibility_text[criterion.source_start : criterion.source_end]
            == criterion.source_text
        )

    _run_database_check(check)


@pytest.mark.integration
def test_ambiguous_and_low_confidence_parse_reasons_are_persisted() -> None:
    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        eligibility_text = (
            "Inclusion Criteria:\n"
            "- Age 18 years or older (unless an exception applies)\n"
            "- Age >= 21"
        )
        version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {"nctId": nct_id},
                    "eligibilityModule": {"eligibilityCriteria": eligibility_text},
                }
            },
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        criteria = list(
            await session.scalars(
                select(Criterion)
                .where(Criterion.trial_version_id == version.id)
                .order_by(Criterion.source_start, Criterion.id)
            )
        )

        assert [criterion.review_reasons for criterion in criteria] == [
            ["ambiguous_clause", "nested_clause"],
            ["low_confidence_parse"],
        ]
        assert [criterion.requires_human_review for criterion in criteria] == [
            True,
            True,
        ]

    _run_database_check(check)


@pytest.mark.integration
def test_reused_matching_snapshot_keeps_criteria_linked_to_its_own_version() -> None:
    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        eligibility_text = "Inclusion Criteria:\n- Age 18 years or older"
        base_study = {
            "protocolSection": {
                "identificationModule": {"nctId": nct_id},
                "eligibilityModule": {"eligibilityCriteria": eligibility_text},
            }
        }
        first_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=base_study,
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        second_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={**base_study, "resultsSection": {"sourceNote": "updated"}},
            retrieved_at=datetime(2026, 8, 27, tzinfo=UTC),
        )
        second_criteria = list(
            await session.scalars(
                select(Criterion).where(Criterion.trial_version_id == second_version.id)
            )
        )

        assert second_version.requires_reparse is False
        assert second_version.matching_reused_from_version_id == first_version.id
        assert len(second_criteria) == 1
        assert second_criteria[0].trial_version_id == second_version.id
        assert second_criteria[0].source_text == "Age 18 years or older"

    _run_database_check(check)
