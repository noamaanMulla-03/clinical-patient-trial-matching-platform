"""Regression coverage for reviewer-correction privacy guard."""

from pathlib import Path


def test_reviewer_correction_migration_restricts_new_reason_codes() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260827_18_reviewer_correction_safety.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260827_18"' in migration
    assert 'down_revision = "20260826_17"' in migration
    assert "ck_review_decisions_reason_code" in migration
    assert "NOT VALID" in migration
