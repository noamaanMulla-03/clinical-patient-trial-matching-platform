"""Regression coverage for pgvector semantic-storage migration operations."""

from pathlib import Path


def test_trial_embedding_migration_enables_vector_and_creates_durable_tables() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260825_15_trial_embeddings.py"
    ).read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "trial_embeddings" in migration
    assert "trial_embedding_jobs" in migration
    assert "Vector(768)" in migration
    assert "vector_cosine_ops" in migration
