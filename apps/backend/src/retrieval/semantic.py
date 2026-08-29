"""Transient patient-query search over versioned public-trial pgvector records."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TrialEmbedding, TrialVersion
from src.retrieval.embedding_encoder import (
    EmbeddingEncoder,
    configured_embedding_encoder,
)
from src.retrieval.schemas import PatientDerivedRetrievalQuery
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
from src.retrieval.trial_documents import (
    SearchableTrial,
    document_from_trial_version,
)


class SemanticRetrievalError(ValueError):
    """Raised when a transient semantic query violates the pinned vector contract."""


class SemanticRetrievalIncompleteError(SemanticRetrievalError):
    """Raised when the declared catalogue lacks complete current-vector coverage."""


@dataclass(frozen=True, slots=True)
class SemanticTrialCandidate:
    """One versioned public-trial snapshot retrieved by semantic similarity."""

    trial: SearchableTrial
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class SemanticCoverage:
    """Non-clinical catalogue coverage needed to make semantic mode auditable."""

    current_trial_count: int
    embedded_trial_count: int

    @property
    def is_complete(self) -> bool:
        return (
            self.current_trial_count > 0
            and self.current_trial_count == self.embedded_trial_count
        )


async def semantic_trial_candidates(
    session: AsyncSession,
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
    catalogue_as_of: datetime,
    encoder: EmbeddingEncoder | None = None,
) -> tuple[SemanticTrialCandidate, ...]:
    """Retrieve current trial vectors with one in-memory synthetic-patient query.

    The query vector is deliberately never persisted. If no current public-trial
    embedding is ready, return no semantic candidates without loading the model.
    """
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")
    if not query.lexical_text:
        return ()
    coverage = await semantic_coverage(session, catalogue_as_of=catalogue_as_of)
    if not coverage.is_complete:
        raise SemanticRetrievalIncompleteError(
            "Semantic retrieval requires complete current-trial vector coverage."
        )

    active_encoder: EmbeddingEncoder
    if encoder is None:
        active_encoder = await asyncio.to_thread(configured_embedding_encoder)
    else:
        active_encoder = encoder
    vector = _validated_query_embedding(
        await asyncio.to_thread(active_encoder.encode, query.lexical_text)
    )
    rows = await session.execute(
        semantic_trial_candidates_statement(
            vector,
            candidate_limit=candidate_limit,
            catalogue_as_of=catalogue_as_of,
        )
    )
    return tuple(
        SemanticTrialCandidate(
            trial=document_from_trial_version(version), score=float(score), rank=rank
        )
        for rank, (version, score) in enumerate(rows, start=1)
    )


def semantic_trial_candidates_statement(
    vector: Sequence[float], *, candidate_limit: int, catalogue_as_of: datetime
) -> Select[tuple[TrialVersion, float]]:
    """Build a bounded cosine query over one immutable catalogue instant."""
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")
    query_vector = _validated_query_embedding(vector)
    distance = TrialEmbedding.embedding.cosine_distance(query_vector)
    score = (1 - distance).label("semantic_score")
    return (
        select(TrialVersion, score)
        .join(TrialEmbedding, TrialEmbedding.trial_version_id == TrialVersion.id)
        .where(
            TrialVersion.ingested_at <= catalogue_as_of,
            (TrialVersion.superseded_at.is_(None))
            | (TrialVersion.superseded_at > catalogue_as_of),
            TrialEmbedding.model_configuration_version
            == SEMANTIC_EMBEDDING_MODEL.configuration_version,
            TrialEmbedding.created_at <= catalogue_as_of,
        )
        .order_by(distance, TrialVersion.nct_id)
        .limit(candidate_limit)
    )


async def semantic_coverage(
    session: AsyncSession, *, catalogue_as_of: datetime
) -> SemanticCoverage:
    """Count model coverage for the catalogue visible to one match run."""
    current_trial_count = int(
        await session.scalar(
            select(func.count())
            .select_from(TrialVersion)
            .where(
                TrialVersion.ingested_at <= catalogue_as_of,
                (TrialVersion.superseded_at.is_(None))
                | (TrialVersion.superseded_at > catalogue_as_of),
            )
        )
        or 0
    )
    embedded_trial_count = int(
        await session.scalar(
            select(func.count())
            .select_from(TrialEmbedding)
            .join(TrialVersion, TrialVersion.id == TrialEmbedding.trial_version_id)
            .where(
                TrialVersion.ingested_at <= catalogue_as_of,
                (TrialVersion.superseded_at.is_(None))
                | (TrialVersion.superseded_at > catalogue_as_of),
                TrialEmbedding.model_configuration_version
                == SEMANTIC_EMBEDDING_MODEL.configuration_version,
                TrialEmbedding.created_at <= catalogue_as_of,
            )
        )
        or 0
    )
    return SemanticCoverage(
        current_trial_count=current_trial_count,
        embedded_trial_count=embedded_trial_count,
    )


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
