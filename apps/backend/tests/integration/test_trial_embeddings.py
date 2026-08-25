"""PostgreSQL checks for durable, public-trial embedding generation jobs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import TrialEmbedding, TrialEmbeddingJob
from app.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
from app.services.source_snapshots import store_trial_version
from app.workers.trial_embeddings import run_queued_trial_embedding_job

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


class _FakeEmbeddingEncoder:
    def __init__(self) -> None:
        self.documents: list[str] = []

    def encode(self, document: str) -> Sequence[float]:
        self.documents.append(document)
        return [0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions


@pytest.mark.integration
def test_new_trial_version_queues_and_stores_one_versioned_public_embedding() -> None:
    """Embedding state is traceable to one immutable trial source version."""

    async def check(session: AsyncSession) -> None:
        nct_id = f"NCT{uuid4().int % 100_000_000:08d}"
        version = await store_trial_version(
            session,
            nct_id=nct_id,
            raw_study={
                "protocolSection": {
                    "identificationModule": {
                        "nctId": nct_id,
                        "briefTitle": "Synthetic melanoma study",
                    },
                    "conditionsModule": {"conditions": ["Melanoma"]},
                    "eligibilityModule": {"eligibilityCriteria": "Adults only"},
                }
            },
            retrieved_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        job = await session.scalar(
            select(TrialEmbeddingJob).where(
                TrialEmbeddingJob.trial_version_id == version.id
            )
        )
        assert job is not None
        assert job.status == "queued"
        assert job.model_configuration_version == (
            SEMANTIC_EMBEDDING_MODEL.configuration_version
        )

        encoder = _FakeEmbeddingEncoder()
        completed_job = await run_queued_trial_embedding_job(
            session, job.id, encoder=encoder
        )
        embedding = await session.scalar(
            select(TrialEmbedding).where(TrialEmbedding.trial_version_id == version.id)
        )

        assert completed_job.status == "completed"
        assert completed_job.failure_code is None
        assert embedding is not None
        assert embedding.model_configuration_version == (
            SEMANTIC_EMBEDDING_MODEL.configuration_version
        )
        assert embedding.content_hash == version.matching_source_hash
        assert list(embedding.embedding) == [0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions
        assert encoder.documents == ["Synthetic melanoma study\nMelanoma\nAdults only"]

    _run_database_check(check)
