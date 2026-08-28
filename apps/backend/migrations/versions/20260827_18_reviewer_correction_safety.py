"""Restrict new reviewer corrections to controlled non-clinical reason codes.

Revision ID: 20260827_18
Revises: 20260826_17
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260827_18"
down_revision = "20260826_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enforce codes on future writes while preserving immutable history."""
    op.execute(
        """
        ALTER TABLE review_decisions
        ADD CONSTRAINT ck_review_decisions_reason_code
        CHECK (reason IN (
            'evidence_missing',
            'evidence_conflicting',
            'evidence_stale',
            'source_span_issue',
            'source_data_issue',
            'other_nonclinical_review_issue'
        )) NOT VALID
        """
    )


def downgrade() -> None:
    """Remove only the new-write safety constraint."""
    op.execute(
        "ALTER TABLE review_decisions "
        "DROP CONSTRAINT ck_review_decisions_reason_code"
    )
