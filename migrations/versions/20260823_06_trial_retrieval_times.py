"""Record the ClinicalTrials.gov response time for each trial source snapshot.

Revision ID: 20260823_06
Revises: 20260823_05
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260823_06"
down_revision = "20260823_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an API response time separate from database ingestion time."""
    op.add_column(
        "trials",
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trial_versions",
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Historical response times were not captured. Their write time is the only
    # available approximation; new records use the client-captured response time.
    op.execute("UPDATE trials SET retrieved_at = ingested_at WHERE retrieved_at IS NULL")
    op.execute(
        "UPDATE trial_versions SET retrieved_at = ingested_at WHERE retrieved_at IS NULL"
    )
    op.alter_column("trials", "retrieved_at", nullable=False)
    op.alter_column("trial_versions", "retrieved_at", nullable=False)


def downgrade() -> None:
    """Remove the explicit API-response receipt times."""
    op.drop_column("trial_versions", "retrieved_at")
    op.drop_column("trials", "retrieved_at")
