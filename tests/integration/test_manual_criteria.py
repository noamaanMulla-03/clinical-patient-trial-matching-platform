"""PostgreSQL checks for source-spanned manual criterion creation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.criteria.manual import (
    ManualCriterionError,
    atomic_criterion_from_record,
    create_manual_criteria,
)
from app.criteria.schemas import AtomicCriterion
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
def test_manual_criterion_creation_requires_exact_eligibility_source_span() -> None:
    """Manual rules are source-linked before automated parsing is introduced."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        source_text = "Adults age 18 years or older."
        version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {"nctId": nct_id},
                    "eligibilityModule": {"eligibilityCriteria": source_text},
                }
            },
            retrieved_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
        criterion = AtomicCriterion(
            category="inclusion",
            source_text="age 18",
            source_start=7,
            source_end=13,
            rule={"kind": "age", "operator": "at_least", "years": 18},
        )

        records = await create_manual_criteria(
            session,
            trial_version_id=version.id,
            criteria=[criterion],
            parser_version="manual-fixture-v1",
        )

        assert len(records) == 1
        assert records[0].trial_version_id == version.id
        assert records[0].source_text == "age 18"
        assert records[0].source_start == 7
        assert records[0].source_end == 13
        assert records[0].parsed_data == {
            "kind": "age",
            "operator": "at_least",
            "years": 18,
        }
        assert atomic_criterion_from_record(records[0]) == criterion

        invalid_criterion = criterion.model_copy(
            update={"source_start": 0, "source_end": 6}
        )
        with pytest.raises(ManualCriterionError):
            await create_manual_criteria(
                session,
                trial_version_id=version.id,
                criteria=[invalid_criterion],
                parser_version="manual-fixture-v1",
            )

    _run_database_check(check)
