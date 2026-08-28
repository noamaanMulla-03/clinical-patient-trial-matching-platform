"""Safety checks for field-separated public-trial TREC embedding documents."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import numpy

from scripts.build_trec_semantic_index import (
    _field_documents_from_xml,
    _write_batch,
)
from src.retrieval.semantic_config import SEMANTIC_EMBEDDING_MODEL

_SOURCE = b"""
<clinical_study>
  <id_info><nct_id>NCT00000001</nct_id></id_info>
  <brief_title>Glucose control study</brief_title>
  <condition>Diabetes mellitus</condition>
  <intervention><intervention_name>Metformin</intervention_name></intervention>
  <eligibility><criteria>
    <textblock>Do not include patients with condition X.</textblock>
  </criteria></eligibility>
</clinical_study>
"""


def test_fielded_documents_preserve_trial_field_boundaries() -> None:
    _, documents = _field_documents_from_xml(_SOURCE)

    assert documents == {
        "title": "Glucose control study",
        "conditions": "Diabetes mellitus",
        "interventions": "Metformin",
        "eligibility": "Do not include patients with condition X.",
    }


def test_fielded_batches_leave_missing_structured_fields_as_zero_vectors(
    tmp_path: Path,
) -> None:
    class FakeEncoder:
        def encode_many(
            self, documents: list[str], *, batch_size: int
        ) -> list[list[float]]:
            assert batch_size == len(documents)
            return [
                [float(index + 1)] * SEMANTIC_EMBEDDING_MODEL.dimensions
                for index, _ in enumerate(documents)
            ]

    paths = {
        field_name: tmp_path / f"{field_name}.f32"
        for field_name in ("title", "conditions")
    }
    vectors = {
        field_name: numpy.memmap(
            path,
            mode="w+",
            dtype=numpy.float32,
            shape=(2, SEMANTIC_EMBEDDING_MODEL.dimensions),
        )
        for field_name, path in paths.items()
    }
    ids = StringIO()

    completed = _write_batch(
        [
            ("NCT00000001", {"title": "Study", "conditions": "Diabetes"}),
            ("NCT00000002", {"title": "", "conditions": "Cancer"}),
        ],
        encoder=FakeEncoder(),
        embeddings=vectors,
        start=0,
        ids_file=ids,
    )

    assert completed == 2
    assert ids.getvalue() == "NCT00000001\nNCT00000002\n"
    assert vectors["title"][0, 0] == 1.0
    assert numpy.all(vectors["title"][1] == 0.0)
    assert vectors["conditions"][0, 0] == 1.0
    assert vectors["conditions"][1, 0] == 2.0
