"""Safety and query-shape checks for semantic candidate retrieval."""

import math
from datetime import UTC, datetime

import pytest

from src.retrieval.semantic import (
    SemanticRetrievalError,
    _validated_query_embedding,
    semantic_trial_candidates_statement,
)
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL


def test_semantic_query_uses_current_versioned_public_trial_embeddings() -> None:
    statement = semantic_trial_candidates_statement(
        [1.0] * SEMANTIC_EMBEDDING_MODEL.dimensions,
        candidate_limit=7,
        catalogue_as_of=datetime(2026, 8, 28, tzinfo=UTC),
    )
    compiled = statement.compile()
    rendered = str(compiled).lower()

    assert "trial_embeddings" in rendered
    assert "trial_versions" in rendered
    assert "superseded_at is null" in rendered
    assert "trial_versions.ingested_at" in rendered
    assert "trial_embeddings.created_at" in rendered
    assert 7 in compiled.params.values()


def test_semantic_query_vector_must_match_the_pinned_contract() -> None:
    valid = _validated_query_embedding([0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions)

    assert valid == [0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions
    with pytest.raises(SemanticRetrievalError, match="unexpected vector size"):
        _validated_query_embedding([0.0])
    with pytest.raises(SemanticRetrievalError, match="invalid data"):
        _validated_query_embedding([math.nan] * SEMANTIC_EMBEDDING_MODEL.dimensions)
    with pytest.raises(ValueError, match="candidate_limit must be positive"):
        semantic_trial_candidates_statement(
            [],
            candidate_limit=0,
            catalogue_as_of=datetime(2026, 8, 28, tzinfo=UTC),
        )
