"""Unit tests for synthetic-data FHIR import protection."""

import pytest

from src.fhir.safety import (
    SYNTHETIC_DATA_TAG_CODE,
    SYNTHETIC_DATA_TAG_SYSTEM,
    SyntheticDataMarkerError,
    require_synthetic_fhir_bundle,
    synthetic_data_tag,
)


def test_accepts_bundle_with_canonical_synthetic_data_tag() -> None:
    bundle = {
        "resourceType": "Bundle",
        "meta": {"tag": [synthetic_data_tag()]},
        "entry": [],
    }

    require_synthetic_fhir_bundle(bundle)


def test_rejects_bundle_without_a_marker() -> None:
    with pytest.raises(SyntheticDataMarkerError, match="meta.tag"):
        require_synthetic_fhir_bundle({"resourceType": "Bundle", "entry": []})


def test_rejects_marker_on_an_entry_instead_of_the_bundle() -> None:
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "meta": {"tag": [synthetic_data_tag()]},
                }
            }
        ],
    }

    with pytest.raises(SyntheticDataMarkerError, match="meta.tag"):
        require_synthetic_fhir_bundle(bundle)


def test_rejects_display_only_marker() -> None:
    bundle = {
        "resourceType": "Bundle",
        "meta": {
            "tag": [
                {"display": "Synthetic data approved for research and demonstration"}
            ]
        },
    }

    with pytest.raises(SyntheticDataMarkerError, match="missing the required"):
        require_synthetic_fhir_bundle(bundle)


def test_rejects_wrong_marker_code_or_system() -> None:
    wrong_code = {
        "resourceType": "Bundle",
        "meta": {
            "tag": [
                {
                    "system": SYNTHETIC_DATA_TAG_SYSTEM,
                    "code": "real-data",
                }
            ]
        },
    }
    wrong_system = {
        "resourceType": "Bundle",
        "meta": {
            "tag": [
                {
                    "system": "urn:another-system",
                    "code": SYNTHETIC_DATA_TAG_CODE,
                }
            ]
        },
    }

    with pytest.raises(SyntheticDataMarkerError):
        require_synthetic_fhir_bundle(wrong_code)
    with pytest.raises(SyntheticDataMarkerError):
        require_synthetic_fhir_bundle(wrong_system)
