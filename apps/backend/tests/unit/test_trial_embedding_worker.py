"""Safety checks for public-trial embedding document construction and validation."""

import math

import pytest

from src.db.models import TrialVersion
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL
from src.workers.trial_embeddings import (
    TrialEmbeddingJobError,
    _embedding_document,
    _validated_embedding,
)


def test_embedding_document_uses_only_current_public_search_fields() -> None:
    version = TrialVersion(
        raw_study={
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT01234567",
                    "briefTitle": "Melanoma treatment study",
                },
                "conditionsModule": {"conditions": ["Melanoma"]},
                "armsInterventionsModule": {
                    "interventions": [
                        {"name": "Synthetic drug", "description": "Study drug"}
                    ]
                },
                "eligibilityModule": {"eligibilityCriteria": "Adults only"},
                "contactsLocationsModule": {
                    "locations": [{"facility": "Do not embed this site name"}]
                },
            }
        }
    )

    document = _embedding_document(version)

    assert document == (
        "Melanoma treatment study\nMelanoma\nSynthetic drug Study drug\nAdults only"
    )
    assert "Do not embed this site name" not in document


def test_embedding_output_must_match_the_pinned_vector_contract() -> None:
    valid = _validated_embedding([0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions)

    assert valid == [0.0] * SEMANTIC_EMBEDDING_MODEL.dimensions
    with pytest.raises(TrialEmbeddingJobError, match="unexpected vector size"):
        _validated_embedding([0.0])
    with pytest.raises(TrialEmbeddingJobError, match="invalid data"):
        _validated_embedding([math.nan] * SEMANTIC_EMBEDDING_MODEL.dimensions)
