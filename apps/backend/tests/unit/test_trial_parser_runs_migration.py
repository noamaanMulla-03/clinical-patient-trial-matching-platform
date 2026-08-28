"""Regression coverage for immutable parser-run provenance storage."""

from pathlib import Path


def test_trial_parser_run_migration_versions_and_protects_raw_output() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260826_17_trial_parser_runs.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260826_17"' in migration
    assert 'down_revision = "20260826_16"' in migration
    assert "trial_parser_runs" in migration
    assert "raw_output" in migration
    assert "protect_trial_parser_runs_history" in migration
