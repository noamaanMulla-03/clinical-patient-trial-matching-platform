"""Generate versioned embeddings for public trial snapshots in durable jobs."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrialEmbedding, TrialEmbeddingJob, TrialVersion
from app.retrieval.embedding_encoder import (
    EmbeddingEncoder,
    EmbeddingEncoderError,
    EmbeddingEncoderUnavailableError,
    configured_embedding_encoder,
)
from app.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
from app.trials.extraction import TrialExtractionError, extract_trial_fields


class TrialEmbeddingJobError(ValueError):
    """Raised when a public-trial embedding job cannot be safely completed."""


async def run_queued_trial_embedding_job(
    session: AsyncSession,
    job_id: UUID,
    *,
    encoder: EmbeddingEncoder | None = None,
) -> TrialEmbeddingJob:
    """Claim and execute one job without exposing public source content in status."""
    claimed_job_id = await session.scalar(
        update(TrialEmbeddingJob)
        .where(TrialEmbeddingJob.id == job_id, TrialEmbeddingJob.status == "queued")
        .values(status="running", started_at=datetime.now(UTC))
        .returning(TrialEmbeddingJob.id)
    )
    job = await session.get(TrialEmbeddingJob, job_id)
    if job is None:
        raise TrialEmbeddingJobError("Trial embedding job was not found.")
    if claimed_job_id is None:
        raise TrialEmbeddingJobError(
            "Trial embedding job is not queued for processing."
        )

    try:
        async with session.begin_nested():
            await _generate_and_store_embedding(session, job, encoder=encoder)
    except Exception as error:
        job.status = "failed"
        job.failure_code, job.failure_message = _safe_failure_details(error)
        job.completed_at = datetime.now(UTC)
    else:
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
    await session.flush()
    return job


async def _generate_and_store_embedding(
    session: AsyncSession,
    job: TrialEmbeddingJob,
    *,
    encoder: EmbeddingEncoder | None,
) -> None:
    version = await session.get(TrialVersion, job.trial_version_id)
    if version is None:
        raise TrialEmbeddingJobError("Trial embedding job has no trial source version.")
    if (
        job.model_configuration_version
        != SEMANTIC_EMBEDDING_MODEL.configuration_version
    ):
        raise TrialEmbeddingJobError(
            "Trial embedding job has an unsupported model version."
        )
    existing = await session.scalar(
        select(TrialEmbedding.id).where(
            TrialEmbedding.trial_version_id == version.id,
            TrialEmbedding.model_configuration_version
            == job.model_configuration_version,
        )
    )
    if existing is not None:
        return

    document = _embedding_document(version)
    active_encoder: EmbeddingEncoder
    if encoder is None:
        active_encoder = await asyncio.to_thread(configured_embedding_encoder)
    else:
        active_encoder = encoder
    vector = _validated_embedding(
        await asyncio.to_thread(active_encoder.encode, document)
    )
    session.add(
        TrialEmbedding(
            id=uuid4(),
            trial_version_id=version.id,
            model_configuration_version=job.model_configuration_version,
            content_hash=version.matching_source_hash,
            embedding=vector,
        )
    )
    await session.flush()


def _embedding_document(version: TrialVersion) -> str:
    """Build a documented public-trial representation without raw-payload copying."""
    try:
        fields = extract_trial_fields(version.raw_study)
    except TrialExtractionError as error:
        raise TrialEmbeddingJobError(
            "Trial embedding job has invalid public trial fields."
        ) from error
    sections = [
        fields.title or "",
        " ".join(fields.conditions),
        " ".join(
            " ".join(
                value
                for value in (
                    intervention.name,
                    intervention.description or "",
                    " ".join(intervention.other_names),
                )
                if value
            )
            for intervention in fields.interventions
        ),
        fields.eligibility_text or "",
    ]
    document = "\n".join(section for section in sections if section.strip())
    if not document:
        raise TrialEmbeddingJobError(
            "Trial embedding job has no public searchable trial text."
        )
    return document


def _validated_embedding(vector: Sequence[float]) -> list[float]:
    """Reject malformed model output before it can enter a searchable vector index."""
    values = list(vector)
    if len(values) != SEMANTIC_EMBEDDING_MODEL.dimensions:
        raise TrialEmbeddingJobError(
            "Configured embedding model returned an unexpected vector size."
        )
    if any(
        isinstance(value, bool) or not math.isfinite(float(value)) for value in values
    ):
        raise TrialEmbeddingJobError(
            "Configured embedding model returned invalid data."
        )
    return [float(value) for value in values]


def _safe_failure_details(error: Exception) -> tuple[str, str]:
    """Keep source text, model output, and exceptions out of durable job state."""
    if isinstance(error, EmbeddingEncoderUnavailableError):
        return "embedding_model_unavailable", "Embedding model could not be loaded."
    if isinstance(error, EmbeddingEncoderError):
        return "embedding_generation_invalid", "Trial embedding could not be generated."
    if isinstance(error, TrialEmbeddingJobError):
        return "embedding_generation_invalid", "Trial embedding could not be generated."
    return "embedding_generation_unexpected", "Trial embedding could not be generated."
