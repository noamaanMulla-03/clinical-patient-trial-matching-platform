"""Add pgvector-backed public trial embeddings and durable generation jobs.

Revision ID: 20260825_15
Revises: 20260823_14
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "20260825_15"
down_revision = "20260823_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store only public-trial vectors tied to immutable source versions."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "trial_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_configuration_version", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trial_version_id"], ["trial_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trial_embeddings_model_configuration",
        "trial_embeddings",
        ["model_configuration_version"],
        unique=False,
    )
    op.create_index(
        "uq_trial_embeddings_version_model",
        "trial_embeddings",
        ["trial_version_id", "model_configuration_version"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_trial_embeddings_embedding_cosine ON trial_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "trial_embedding_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_configuration_version", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="queued", nullable=False
        ),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_trial_embedding_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["trial_version_id"], ["trial_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trial_embedding_jobs_status_created_at",
        "trial_embedding_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_trial_embedding_jobs_version_model",
        "trial_embedding_jobs",
        ["trial_version_id", "model_configuration_version"],
        unique=True,
    )


def downgrade() -> None:
    """Remove only the Phase 8 semantic-storage tables and vector extension use."""
    op.drop_index("uq_trial_embedding_jobs_version_model", "trial_embedding_jobs")
    op.drop_index("ix_trial_embedding_jobs_status_created_at", "trial_embedding_jobs")
    op.drop_table("trial_embedding_jobs")
    op.execute("DROP INDEX ix_trial_embeddings_embedding_cosine")
    op.drop_index("uq_trial_embeddings_version_model", "trial_embeddings")
    op.drop_index("ix_trial_embeddings_model_configuration", "trial_embeddings")
    op.drop_table("trial_embeddings")
    op.execute("DROP EXTENSION IF EXISTS vector")
