"""Transient patient-query search over versioned public-trial pgvector records."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Trial, TrialEmbedding, TrialVersion
from src.retrieval.embedding_encoder import (
    EmbeddingEncoder,
    configured_embedding_encoder,
)
from src.retrieval.schemas import PatientDerivedRetrievalQuery
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


class SemanticRetrievalError(ValueError):
    """Raised when a transient semantic query violates the pinned vector contract."""


@dataclass(frozen=True, slots=True)
class SemanticTrialCandidate:
    """One current public-trial projection retrieved by semantic similarity."""

    trial: Trial
    score: float
    rank: int


async def semantic_trial_candidates(
    session: AsyncSession,
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
    encoder: EmbeddingEncoder | None = None,
) -> tuple[SemanticTrialCandidate, ...]:
    """Retrieve current trial vectors with one in-memory synthetic-patient query.

    The query vector is deliberately never persisted. If no current public-trial
    embedding is ready, return no semantic candidates without loading the model.
    """
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")
    if not query.lexical_text or not await _has_current_embeddings(session):
        return ()

    active_encoder: EmbeddingEncoder
    if encoder is None:
        active_encoder = await asyncio.to_thread(configured_embedding_encoder)
    else:
        active_encoder = encoder
    vector = _validated_query_embedding(
        await asyncio.to_thread(active_encoder.encode, query.lexical_text)
    )
    rows = await session.execute(
        semantic_trial_candidates_statement(vector, candidate_limit=candidate_limit)
    )
    return tuple(
        SemanticTrialCandidate(trial=trial, score=float(score), rank=rank)
        for rank, (trial, score) in enumerate(rows, start=1)
    )


def semantic_trial_candidates_statement(
    vector: Sequence[float], *, candidate_limit: int
) -> Select[tuple[Trial, float]]:
    """Build a bounded cosine-similarity query over current source versions only."""
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")
    query_vector = _validated_query_embedding(vector)
    distance = TrialEmbedding.embedding.cosine_distance(query_vector)
    score = (1 - distance).label("semantic_score")
    return (
        select(Trial, score)
        .join(TrialVersion, TrialVersion.nct_id == Trial.nct_id)
        .join(TrialEmbedding, TrialEmbedding.trial_version_id == TrialVersion.id)
        .where(
            TrialVersion.superseded_at.is_(None),
            TrialEmbedding.model_configuration_version
            == SEMANTIC_EMBEDDING_MODEL.configuration_version,
        )
        .order_by(distance, Trial.nct_id)
        .limit(candidate_limit)
    )


async def _has_current_embeddings(session: AsyncSession) -> bool:
    return (
        await session.scalar(
            select(TrialEmbedding.id)
            .join(TrialVersion, TrialVersion.id == TrialEmbedding.trial_version_id)
            .where(
                TrialVersion.superseded_at.is_(None),
                TrialEmbedding.model_configuration_version
                == SEMANTIC_EMBEDDING_MODEL.configuration_version,
            )
            .limit(1)
        )
    ) is not None


def _validated_query_embedding(vector: Sequence[float]) -> list[float]:
    """Reject malformed local output before PostgreSQL receives a vector query."""
    values = list(vector)
    if len(values) != SEMANTIC_EMBEDDING_MODEL.dimensions:
        raise SemanticRetrievalError(
            "Configured embedding model returned an unexpected vector size."
        )
    if any(
        isinstance(value, bool) or not math.isfinite(float(value)) for value in values
    ):
        raise SemanticRetrievalError(
            "Configured embedding model returned invalid data."
        )
    return [float(value) for value in values]
