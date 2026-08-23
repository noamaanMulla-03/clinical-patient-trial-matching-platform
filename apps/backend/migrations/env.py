"""Alembic migration environment for the clinical trial matcher database."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Keep migrations self-contained with the backend package after a local checkout.
backend_path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_path))

import app.db.models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _migration_database_url() -> str:
    """Return a synchronous PostgreSQL URL for Alembic's migration engine."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL must be set before running a migration. "
            "Copy .env.example to .env and export DATABASE_URL, or set it inline."
        )

    # The application will use asyncpg; Alembic runs synchronously via psycopg.
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


config.set_main_option("sqlalchemy.url", _migration_database_url())
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without connecting to the database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a synchronous PostgreSQL connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
