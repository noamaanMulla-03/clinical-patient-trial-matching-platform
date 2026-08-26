"""Tests for preparing approved synthetic FHIR R4 fixture Bundles."""

import importlib.util
import json
from pathlib import Path

import pytest

from src.fhir.safety import (
    SYNTHETIC_DATA_TAG_CODE,
    SYNTHETIC_DATA_TAG_SYSTEM,
    require_synthetic_fhir_bundle,
)

PREPARATION_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "datasets"
    / "scripts"
    / "prepare_synthea_fixtures.py"
)


def _preparation_module() -> object:
    specification = importlib.util.spec_from_file_location(
        "prepare_synthea_fixtures", PREPARATION_SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_preparation_adds_the_required_marker_to_a_single_patient_bundle(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.json"
    source_path.write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "synthetic-1"}}
                ],
            }
        ),
        encoding="utf-8",
    )
    module = _preparation_module()

    output_path = module.prepare_fixtures(
        [source_path], output_directory=tmp_path / "prepared"
    )[0]
    bundle = json.loads(output_path.read_text(encoding="utf-8"))

    require_synthetic_fhir_bundle(bundle)
    assert bundle["meta"]["tag"] == [
        {
            "system": SYNTHETIC_DATA_TAG_SYSTEM,
            "code": SYNTHETIC_DATA_TAG_CODE,
            "display": "Synthetic data approved for research and demonstration",
        }
    ]


def test_preparation_rejects_bundles_without_exactly_one_patient(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.json"
    source_path.write_text(
        json.dumps({"resourceType": "Bundle", "entry": []}), encoding="utf-8"
    )
    module = _preparation_module()

    with pytest.raises(ValueError, match="exactly one Patient"):
        module.prepare_fixtures([source_path], output_directory=tmp_path / "prepared")
