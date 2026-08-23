"""Unit tests for database-model foundations."""

from app.db.base import Base


def test_base_exposes_metadata_for_registered_database_models() -> None:
    """Database models register their tables through the shared base class."""
    assert Base.metadata is not None
