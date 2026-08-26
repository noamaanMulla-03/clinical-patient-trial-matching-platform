"""PostgreSQL checks for transient semantic retrieval over public trial vectors."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import TrialEmbedding
from app.retrieval.schemas import PatientDerivedRetrievalQuery, RetrievalTerm
from app.retrieval.semantic import semantic_trial_candidates
from app.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
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


class _FakeQueryEncoder:
    def encode(self, _: str) -> Sequence[float]:
        return [1.0] + [0.0] * (SEMANTIC_EMBEDDING_MODEL.dimensions - 1)


@pytest.mark.integration
def test_semantic_retrieval_uses_current_public_trial_vectors() -> None:
    """The in-memory query retrieves its exact current public trial snapshot."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {
                        "nctId": nct_id,
                        "briefTitle": "Public semantic retrieval fixture",
                    },
                    "eligibilityModule": {"eligibilityCriteria": "Adults only"},
                }
            },
            retrieved_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        session.add(
            TrialEmbedding(
                trial_version_id=version.id,
                model_configuration_version=(
                    SEMANTIC_EMBEDDING_MODEL.configuration_version
                ),
                content_hash=version.matching_source_hash,
                embedding=[1.0] + [0.0] * (SEMANTIC_EMBEDDING_MODEL.dimensions - 1),
            )
        )
        await session.flush()

        candidates = await semantic_trial_candidates(
            session,
            PatientDerivedRetrievalQuery(
                terms=[
                    RetrievalTerm(
                        text="different synthetic wording",
                        source_fact_id="fact-1",
                        kind="condition",
                    )
                ]
            ),
            candidate_limit=100,
            encoder=_FakeQueryEncoder(),
        )

        candidate = next(item for item in candidates if item.trial.nct_id == nct_id)
        assert candidate.rank >= 1
        assert candidate.score == pytest.approx(1.0)

    _run_database_check(check)
