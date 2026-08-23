"""Regression checks for PostgreSQL lexical-index migration coverage."""

from pathlib import Path


def test_trial_full_text_migration_indexes_each_searchable_projection_field() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260823_12_trial_full_text_indexes.py"
    ).read_text(encoding="utf-8")

    assert "ix_trials_title_fts" in migration
    assert "ix_trials_conditions_fts" in migration
    assert "ix_trials_interventions_fts" in migration
    assert "ix_trials_eligibility_text_fts" in migration
    assert migration.count("USING gin") == 4
