"""PostgreSQL exit checks for trial source history and derived-work invalidation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Criterion, Trial
from app.services.source_snapshots import store_trial_version

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
def test_changed_eligibility_text_requires_fresh_derived_work() -> None:
    """Eligibility changes cannot reuse criteria tied to an earlier source version."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        original_eligibility_text = "Inclusion Criteria: adults age 18 years or older."
        original_study = {
            "protocolSection": {
                "identificationModule": {"nctId": nct_id},
                "eligibilityModule": {"eligibilityCriteria": original_eligibility_text},
            }
        }
        changed_study = deepcopy(original_study)
        changed_eligibility_text = "Inclusion Criteria: adults age 21 years or older."
        changed_study["protocolSection"]["eligibilityModule"]["eligibilityCriteria"] = (
            changed_eligibility_text
        )

        original_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=original_study,
            retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        original_criterion = Criterion(
            id=uuid4(),
            trial_version_id=original_version.id,
            category="inclusion",
            source_text=original_eligibility_text,
            source_start=0,
            source_end=len(original_eligibility_text),
            parsed_data={"kind": "age"},
            parser_version="test-parser-v1",
        )
        session.add(original_criterion)
        await session.flush()

        changed_version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study=changed_study,
            retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        trial = await session.get(Trial, nct_id)
        original_criteria = list(
            await session.scalars(
                select(Criterion).where(
                    Criterion.trial_version_id == original_version.id
                )
            )
        )
        changed_criteria = list(
            await session.scalars(
                select(Criterion).where(
                    Criterion.trial_version_id == changed_version.id
                )
            )
        )

        assert trial is not None
        assert trial.eligibility_text == changed_eligibility_text
        assert original_version.source_hash != changed_version.source_hash
        assert (
            original_version.matching_source_hash
            != changed_version.matching_source_hash
        )
        assert original_version.superseded_by_version_id == changed_version.id
        assert changed_version.requires_reparse is True
        assert changed_version.matching_reused_from_version_id is None
        # Historical criteria remain traceable to their immutable source version;
        # they are never copied onto the changed version while it awaits reparsing.
        assert original_criteria == [original_criterion]
        assert changed_criteria == []

    _run_database_check(check)
