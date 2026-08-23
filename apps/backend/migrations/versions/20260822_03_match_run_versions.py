"""Add immutable, queryable match-run configuration versions.

Revision ID: 20260822_03
Revises: 20260822_02
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260822_03"
down_revision = "20260822_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add queryable version fields and prevent changing a run's configuration."""
    op.add_column(
        "match_runs",
        sa.Column("parser_version", sa.String(length=128), nullable=False),
    )
    op.add_column(
        "match_runs",
        sa.Column("retrieval_version", sa.String(length=128), nullable=False),
    )
    op.add_column(
        "match_runs",
        sa.Column("rule_engine_version", sa.String(length=128), nullable=False),
    )
    op.add_column(
        "match_runs",
        sa.Column("terminology_mapping_version", sa.String(length=128), nullable=False),
    )
    op.add_column(
        "match_runs",
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
    )
    op.add_column(
        "match_runs",
        sa.Column("model_configuration_version", sa.String(length=128), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_match_run_configuration_changes()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.configuration_snapshot IS DISTINCT FROM OLD.configuration_snapshot
               OR NEW.parser_version IS DISTINCT FROM OLD.parser_version
               OR NEW.retrieval_version IS DISTINCT FROM OLD.retrieval_version
               OR NEW.rule_engine_version IS DISTINCT FROM OLD.rule_engine_version
               OR NEW.terminology_mapping_version
                  IS DISTINCT FROM OLD.terminology_mapping_version
               OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
               OR NEW.model_configuration_version
                  IS DISTINCT FROM OLD.model_configuration_version THEN
                RAISE EXCEPTION 'match run configuration is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_match_run_configuration
        BEFORE UPDATE OF configuration_snapshot, parser_version, retrieval_version,
            rule_engine_version, terminology_mapping_version, prompt_version,
            model_configuration_version ON match_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_match_run_configuration_changes();
        """
    )


def downgrade() -> None:
    """Remove immutable match-run version fields and their guard trigger."""
    op.execute("DROP TRIGGER protect_match_run_configuration ON match_runs")
    op.execute("DROP FUNCTION prevent_match_run_configuration_changes()")
    op.drop_column("match_runs", "model_configuration_version")
    op.drop_column("match_runs", "prompt_version")
    op.drop_column("match_runs", "terminology_mapping_version")
    op.drop_column("match_runs", "rule_engine_version")
    op.drop_column("match_runs", "retrieval_version")
    op.drop_column("match_runs", "parser_version")
