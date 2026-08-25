"""Checks for the immutable Phase 8 embedding-model selection."""

from app.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


def test_embedding_model_selection_is_pinned_and_complete() -> None:
    """A mutable model alias must never define a semantic index or match run."""
    assert SEMANTIC_EMBEDDING_MODEL.snapshot() == {
        "configuration_version": "pubmedbert-embeddings-v1",
        "repository": "NeuML/pubmedbert-base-embeddings",
        "revision": "b79526d6ef3645e0df4530322e266f24c829f5ef",
        "dimensions": 768,
        "max_input_tokens": 512,
        "normalize_embeddings": True,
        "license": "Apache-2.0",
    }
    assert len(SEMANTIC_EMBEDDING_MODEL.revision) == 40
