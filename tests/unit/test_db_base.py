"""Unit tests for database-model foundations."""

from app.db.base import Base


def test_base_exposes_empty_metadata_before_models_are_added() -> None:
    """Future models can register their tables through the shared base class."""
    assert Base.metadata.tables == {}
