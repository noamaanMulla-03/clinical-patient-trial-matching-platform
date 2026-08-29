"""Regression checks for the read-only TREC semantic comparison output."""

import pytest

from scripts.evaluate_trec_hybrid import (
    _field_weights,
    _fuse,
    _fuse_ranked_lists,
    _rerank_with_structured_support,
    _result,
    _semantic_ids,
    _semantic_vectors,
    _trial_count,
)
from src.db.models import Trial


def test_trec_result_retains_only_bounded_public_trial_identifiers() -> None:
    ranked = ["NCT00000001", "NCT00000002"]

    result = _result(
        "topic-1",
        ranked,
        {"NCT00000001": 2, "NCT00000002": 0},
        12.5,
    )

    assert result["topic_id"] == "topic-1"
    assert result["ranked_nct_ids"] == ranked
    assert result["latency_ms"] == 12.5


def test_trec_preview_limit_must_be_a_completed_bounded_slice() -> None:
    assert _trial_count(500, completed_trials=1_000) == 500
    with pytest.raises(SystemExit, match="At least 100"):
        _trial_count(99, completed_trials=1_000)
    with pytest.raises(SystemExit, match="exceeds the completed index"):
        _trial_count(1_001, completed_trials=1_000)


def test_trec_adapter_reranks_but_keeps_unknown_candidates() -> None:
    reranked = _rerank_with_structured_support(
        ["NCT00000001", "NCT00000002"],
        topic_id="2022-1",
        topic_text="Melanoma treatment",
        trials_by_nct={
            "NCT00000001": Trial(nct_id="NCT00000001", conditions=[], interventions=[]),
            "NCT00000002": Trial(
                nct_id="NCT00000002",
                conditions=["Melanoma"],
                interventions=[],
            ),
        },
    )

    assert reranked == ["NCT00000002", "NCT00000001"]


def test_fielded_evaluation_can_use_rank_only_field_fusion() -> None:
    assert _fuse_ranked_lists(
        [(["NCT00000001", "NCT00000002"], 2.0), (["NCT00000003"], 1.0)]
    )[:2] == ["NCT00000001", "NCT00000002"]
    assert _fuse(
        ["NCT00000001"], ["NCT00000002"], lexical_weight=1.0, semantic_weight=4.0
    )[:1] == ["NCT00000002"]
    assert _field_weights(["conditions=2.0"])["conditions"] == 2.0


def test_legacy_full_text_index_is_explicitly_not_field_weighted(tmp_path) -> None:
    import numpy

    index_path = tmp_path / "vectors.f32"
    vectors = numpy.zeros((100, 768), dtype=numpy.float32)
    vectors[0, 0] = 1.0
    vectors[1, 1] = 1.0
    vectors.tofile(index_path)
    manifest = {"embedding_file": index_path.name}
    vectors, representation = _semantic_vectors(
        tmp_path, manifest=manifest, trial_count=100
    )

    assert representation == "legacy-combined-public-trial-text"
    assert (
        _semantic_ids(
            vectors,
            query=numpy.asarray([1.0] + [0.0] * 767, dtype=numpy.float32),
            ids=[f"NCT{index:08d}" for index in range(100)],
            field_weights=_field_weights([]),
            fusion="weighted-rrf",
        )[0]
        == "NCT00000000"
    )
