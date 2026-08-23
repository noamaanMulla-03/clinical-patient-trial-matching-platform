# Database migrations

This directory is managed by Alembic. The application will use SQLAlchemy's asynchronous `asyncpg` driver, while Alembic converts the same `DATABASE_URL` to the synchronous `psycopg` driver when applying migrations.

## One-time local setup

```bash
cd apps/backend
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Docker Compose does not automatically export `.env` into an interactive shell. Load the database URL before running Alembic locally:

```bash
set -a
source .env
set +a
```

## Commands

Create a revision after adding or changing SQLAlchemy models:

```bash
.venv/bin/alembic revision --autogenerate -m "describe schema change"
```

Review the generated file in `migrations/versions/` before applying it. Autogeneration is a draft, not an approval mechanism.

Apply all pending migrations:

```bash
.venv/bin/alembic upgrade head
```

Revert the latest migration:

```bash
.venv/bin/alembic downgrade -1
```

Show the database's current migration revision:

```bash
.venv/bin/alembic current
```

## Current state

The current migration history creates the source-linked patient, trial, match-run,
criterion-result, and review-decision tables. Commit every migration file; do not
edit a migration that has already been applied outside a disposable local database.
