"""The pinned embedding-model contract for the upcoming semantic retrieval path."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingModelConfiguration:
    """One immutable model choice that future embedding jobs must snapshot."""

    configuration_version: str
    repository: str
    revision: str
    dimensions: int
    max_input_tokens: int
    normalize_embeddings: bool
    license: str

    def snapshot(self) -> dict[str, object]:
        """Return only non-clinical model metadata for future match-run provenance."""
        return asdict(self)


# PubMedBERT is selected for biomedical language coverage. The exact Hub commit,
# vector size, input limit, and normalization policy are frozen before any trial
# or patient embedding is generated; changing one requires a new configuration.
SEMANTIC_EMBEDDING_MODEL = EmbeddingModelConfiguration(
    configuration_version="pubmedbert-embeddings-v1",
    repository="NeuML/pubmedbert-base-embeddings",
    revision="b79526d6ef3645e0df4530322e266f24c829f5ef",
    dimensions=768,
    max_input_tokens=512,
    normalize_embeddings=True,
    license="Apache-2.0",
)
