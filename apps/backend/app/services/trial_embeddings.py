"""Durable job creation for versioned public-trial semantic embeddings."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrialEmbeddingJob, TrialVersion
from app.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


class TrialEmbeddingQueueError(ValueError):
    """Raised when a public trial snapshot cannot receive an embedding job."""


async def queue_trial_embedding_job(
    session: AsyncSession, *, trial_version_id: UUID
) -> TrialEmbeddingJob:
    """Create one idempotent job for the pinned embedding-model configuration."""
    if await session.get(TrialVersion, trial_version_id) is None:
        raise TrialEmbeddingQueueError("Trial embedding requires a trial version.")
    job_id = await session.scalar(
        insert(TrialEmbeddingJob)
        .values(
            trial_version_id=trial_version_id,
            model_configuration_version=(
                SEMANTIC_EMBEDDING_MODEL.configuration_version
            ),
            status="queued",
        )
        .on_conflict_do_nothing(
            index_elements=(
                TrialEmbeddingJob.trial_version_id,
                TrialEmbeddingJob.model_configuration_version,
            )
        )
        .returning(TrialEmbeddingJob.id)
    )
    if job_id is not None:
        job = await session.get(TrialEmbeddingJob, job_id)
        if job is not None:
            return job
    existing = await session.scalar(
        select(TrialEmbeddingJob)
        .where(
            TrialEmbeddingJob.trial_version_id == trial_version_id,
            TrialEmbeddingJob.model_configuration_version
            == SEMANTIC_EMBEDDING_MODEL.configuration_version,
        )
        .limit(1)
    )
    if existing is None:
        raise TrialEmbeddingQueueError("Trial embedding job could not be queued.")
    return existing


async def queue_next_missing_trial_embedding_job(
    session: AsyncSession,
) -> TrialEmbeddingJob | None:
    """Backfill one older immutable version without delaying matching work.

    New versions queue a job as part of ingestion. This bounded fallback lets a
    worker add jobs for source versions that existed before semantic storage was
    introduced, while preserving the same immutable version and model contract.
    """
    has_model_job = exists(
        select(TrialEmbeddingJob.id).where(
            TrialEmbeddingJob.trial_version_id == TrialVersion.id,
            TrialEmbeddingJob.model_configuration_version
            == SEMANTIC_EMBEDDING_MODEL.configuration_version,
        )
    )
    trial_version_id = await session.scalar(
        select(TrialVersion.id)
        .where(~has_model_job)
        .order_by(TrialVersion.ingested_at, TrialVersion.id)
        .limit(1)
    )
    if trial_version_id is None:
        return None
    return await queue_trial_embedding_job(session, trial_version_id=trial_version_id)
