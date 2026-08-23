"""Add safe terminal failure details for match runs.

Revision ID: 20260823_13
Revises: 20260823_12
Create Date: 2026-08-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260823_13"
down_revision = "20260823_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Retain static failure details without modifying immutable run inputs."""
    op.add_column("match_runs", sa.Column("failure_code", sa.String(length=64)))
    op.add_column("match_runs", sa.Column("failure_message", sa.Text()))


def downgrade() -> None:
    """Remove the match-run operational failure fields."""
    op.drop_column("match_runs", "failure_message")
    op.drop_column("match_runs", "failure_code")
