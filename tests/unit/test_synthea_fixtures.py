"""Checks that committed Synthea fixtures remain safe FHIR R4 import inputs."""

import json
from hashlib import sha256
from pathlib import Path

from app.fhir.schemas import FHIRImportRequest

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[2] / "datasets" / "fhir-r4"


def test_committed_synthea_fixtures_are_marked_single_patient_bundles() -> None:
    fixture_paths = sorted(FIXTURE_DIRECTORY.glob("synthea-r4-patient-*.json"))
    manifest = json.loads(
        (FIXTURE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
    )

    assert len(fixture_paths) == 3
    assert [fixture_path.name for fixture_path in fixture_paths] == [
        fixture["path"] for fixture in manifest["fixtures"]
    ]
    for fixture_path in fixture_paths:
        bundle = json.loads(fixture_path.read_text(encoding="utf-8"))
        request = FHIRImportRequest(bundle=bundle)
        patient_resources = [
            entry["resource"]
            for entry in request.bundle["entry"]
            if entry.get("resource", {}).get("resourceType") == "Patient"
        ]

        assert len(patient_resources) == 1
        assert isinstance(patient_resources[0].get("id"), str)
        fixture_manifest = next(
            fixture
            for fixture in manifest["fixtures"]
            if fixture["path"] == fixture_path.name
        )
        assert (
            sha256(fixture_path.read_bytes()).hexdigest()
            == fixture_manifest["prepared_sha256"]
        )
