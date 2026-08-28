"""Regression coverage for parser-review reason storage."""

from pathlib import Path


def test_criterion_review_reason_migration_adds_immutable_reason_codes() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260826_16_criterion_review_reasons.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260826_16"' in migration
    assert 'down_revision = "20260825_15"' in migration
    assert '"review_reasons"' in migration
    assert "JSONB" in migration
