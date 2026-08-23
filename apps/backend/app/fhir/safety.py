"""Safety checks applied at the synthetic FHIR import boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FHIR_BUNDLE_RESOURCE_TYPE = "Bundle"
SYNTHETIC_DATA_TAG_SYSTEM = "urn:clinical-trial-matcher:data-classification"
SYNTHETIC_DATA_TAG_CODE = "synthetic-data"
SYNTHETIC_DATA_TAG_DISPLAY = "Synthetic data approved for research and demonstration"


class SyntheticDataMarkerError(ValueError):
    """Raised when an import payload is not explicitly marked as synthetic."""


def synthetic_data_tag() -> dict[str, str]:
    """Return the canonical FHIR tag required on an import Bundle."""
    return {
        "system": SYNTHETIC_DATA_TAG_SYSTEM,
        "code": SYNTHETIC_DATA_TAG_CODE,
        "display": SYNTHETIC_DATA_TAG_DISPLAY,
    }


def require_synthetic_fhir_bundle(bundle: Mapping[str, Any]) -> None:
    """Reject a FHIR import unless its Bundle metadata has the exact marker.

    Importers must call this before persisting, normalizing, logging, or
    enqueueing any resource from the submitted Bundle.
    """
    if bundle.get("resourceType") != FHIR_BUNDLE_RESOURCE_TYPE:
        raise SyntheticDataMarkerError(
            "FHIR import payload must be a Bundle with the required synthetic-data tag."
        )

    meta = bundle.get("meta")
    if not isinstance(meta, Mapping):
        raise SyntheticDataMarkerError(
            "FHIR Bundle meta.tag must contain the required synthetic-data tag."
        )

    tags = meta.get("tag")
    if not isinstance(tags, list):
        raise SyntheticDataMarkerError(
            "FHIR Bundle meta.tag must contain the required synthetic-data tag."
        )

    for tag in tags:
        if not isinstance(tag, Mapping):
            continue
        if (
            tag.get("system") == SYNTHETIC_DATA_TAG_SYSTEM
            and tag.get("code") == SYNTHETIC_DATA_TAG_CODE
        ):
            return

    raise SyntheticDataMarkerError(
        "FHIR Bundle is missing the required synthetic-data tag. "
        "Real or unmarked patient data cannot be imported."
    )
