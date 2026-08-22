"""Async SQLAlchemy session dependency for database-backed API operations."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database-backed route is used without a configured database."""


@lru_cache
def session_factory_for(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Create one reusable session factory for a configured async database URL."""
    return async_sessionmaker(
        create_async_engine(database_url, pool_pre_ping=True),
        expire_on_commit=False,
    )


async def get_database_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session; the route owns its transaction boundary."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise DatabaseConfigurationError(
            "DATABASE_URL must be configured for database-backed API routes."
        )

    async with session_factory_for(database_url)() as session:
        yield session
