"""Pinned local encoder shared by public-trial and transient-query embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


class EmbeddingEncoderError(ValueError):
    """Raised when the configured local encoder cannot produce a vector."""


class EmbeddingEncoderUnavailableError(EmbeddingEncoderError):
    """Raised when the pinned local encoder cannot be loaded."""


class EmbeddingEncoder(Protocol):
    """Minimal encoder surface for deterministic worker and retrieval tests."""

    def encode(self, document: str) -> Sequence[float]: ...


class SentenceTransformerEmbeddingEncoder:
    """Load the pinned model locally; embedding content never leaves this process."""

    def __init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise EmbeddingEncoderUnavailableError(
                "The configured embedding package is unavailable."
            ) from error
        try:
            self._model = SentenceTransformer(
                SEMANTIC_EMBEDDING_MODEL.repository,
                revision=SEMANTIC_EMBEDDING_MODEL.revision,
            )
        except Exception as error:
            raise EmbeddingEncoderUnavailableError(
                "The configured embedding model could not be loaded."
            ) from error

    def encode(self, document: str) -> Sequence[float]:
        """Generate one normalized vector without logging source or query text."""
        try:
            vector = self._model.encode(
                document,
                normalize_embeddings=SEMANTIC_EMBEDDING_MODEL.normalize_embeddings,
                show_progress_bar=False,
            )
            return [float(value) for value in vector]
        except Exception as error:
            raise EmbeddingEncoderError(
                "The configured embedding model could not encode a document."
            ) from error


@lru_cache(maxsize=1)
def configured_embedding_encoder() -> SentenceTransformerEmbeddingEncoder:
    """Cache one pinned encoder per worker process."""
    return SentenceTransformerEmbeddingEncoder()
