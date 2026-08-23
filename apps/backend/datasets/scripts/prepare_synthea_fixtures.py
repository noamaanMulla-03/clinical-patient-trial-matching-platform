"""Prepare explicitly approved Synthea FHIR R4 Bundles for this project."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SYNTHETIC_TAG = {
    "system": "urn:clinical-trial-matcher:data-classification",
    "code": "synthetic-data",
    "display": "Synthetic data approved for research and demonstration",
}


def prepare_bundle(source_path: Path) -> dict[str, Any]:
    """Load one Synthea Bundle and add this application's required approval tag."""
    bundle = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError(f"{source_path} is not a FHIR Bundle.")

    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise ValueError(f"{source_path} does not contain FHIR Bundle entries.")
    patient_count = sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("resource"), dict)
        and entry["resource"].get("resourceType") == "Patient"
    )
    if patient_count != 1:
        raise ValueError(f"{source_path} must contain exactly one Patient resource.")

    meta = bundle.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise ValueError(f"{source_path} has an invalid top-level Bundle meta field.")
    tags = meta.setdefault("tag", [])
    if not isinstance(tags, list):
        raise ValueError(
            f"{source_path} has an invalid top-level Bundle meta.tag field."
        )

    # The importer will never create this marker: fixture preparation does so only
    # after the source Bundle has been selected from the known Synthea archive.
    if not any(
        isinstance(tag, dict)
        and tag.get("system") == SYNTHETIC_TAG["system"]
        and tag.get("code") == SYNTHETIC_TAG["code"]
        for tag in tags
    ):
        tags.append(SYNTHETIC_TAG)
    return bundle


def prepare_fixtures(
    source_paths: Sequence[Path], *, output_directory: Path
) -> list[Path]:
    """Write marked JSON Bundles using source-neutral fixture filenames."""
    output_directory.mkdir(parents=True, exist_ok=True)
    prepared_paths: list[Path] = []
    for index, source_path in enumerate(source_paths, start=1):
        output_path = output_directory / f"synthea-r4-patient-{index:02d}.json"
        output_path.write_text(
            json.dumps(prepare_bundle(source_path), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        prepared_paths.append(output_path)
    return prepared_paths


def main() -> None:
    """Prepare one or more selected Synthea Bundles from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source_paths", nargs="+", type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    prepare_fixtures(
        arguments.source_paths, output_directory=arguments.output_directory
    )


if __name__ == "__main__":
    main()
