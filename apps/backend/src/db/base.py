"""Shared SQLAlchemy declarative base for all database models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class whose metadata Alembic inspects for schema changes."""
