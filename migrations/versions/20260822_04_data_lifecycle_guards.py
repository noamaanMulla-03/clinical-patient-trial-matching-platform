"""Enforce current-state and historical-snapshot data lifecycles.

Revision ID: 20260822_04
Revises: 20260822_03
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260822_04"
down_revision = "20260822_03"
branch_labels = None
depends_on = None

IMMUTABLE_SNAPSHOT_TABLES = (
    "patient_facts",
    "trial_versions",
    "criteria",
    "criterion_results",
    "review_decisions",
)


def upgrade() -> None:
    """Protect historical inputs and outputs while retaining operational state updates."""
    op.execute(
        """
        CREATE FUNCTION prevent_historical_snapshot_changes()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% records are immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in IMMUTABLE_SNAPSHOT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER protect_{table_name}_history
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_historical_snapshot_changes();
            """
        )

    op.execute(
        """
        CREATE FUNCTION prevent_patient_import_source_changes()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.fhir_version IS DISTINCT FROM OLD.fhir_version
               OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
               OR NEW.source_bundle IS DISTINCT FROM OLD.source_bundle
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'patient import source snapshot is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_patient_import_source
        BEFORE UPDATE OR DELETE ON patient_imports
        FOR EACH ROW EXECUTE FUNCTION prevent_patient_import_source_changes();
        """
    )

    op.execute("DROP TRIGGER protect_match_run_configuration ON match_runs")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_match_run_configuration_changes()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.patient_import_id IS DISTINCT FROM OLD.patient_import_id
               OR NEW.configuration_snapshot IS DISTINCT FROM OLD.configuration_snapshot
               OR NEW.parser_version IS DISTINCT FROM OLD.parser_version
               OR NEW.retrieval_version IS DISTINCT FROM OLD.retrieval_version
               OR NEW.rule_engine_version IS DISTINCT FROM OLD.rule_engine_version
               OR NEW.terminology_mapping_version
                  IS DISTINCT FROM OLD.terminology_mapping_version
               OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
               OR NEW.model_configuration_version
                  IS DISTINCT FROM OLD.model_configuration_version
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
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
        BEFORE UPDATE OF patient_import_id, configuration_snapshot, parser_version,
            retrieval_version, rule_engine_version, terminology_mapping_version,
            prompt_version, model_configuration_version, created_at ON match_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_match_run_configuration_changes();
        """
    )

    op.execute(
        """
        CREATE FUNCTION prevent_trial_match_history_changes()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR NEW.match_run_id IS DISTINCT FROM OLD.match_run_id
               OR NEW.trial_version_id IS DISTINCT FROM OLD.trial_version_id
               OR NEW.candidate_rank IS DISTINCT FROM OLD.candidate_rank
               OR NEW.retrieval_scores IS DISTINCT FROM OLD.retrieval_scores
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'trial match retrieval inputs are immutable';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM match_runs
                WHERE id = OLD.match_run_id
                  AND status IN ('completed', 'failed', 'cancelled')
            ) THEN
                RAISE EXCEPTION 'trial match is immutable after its run is terminal';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_trial_match_history
        BEFORE UPDATE OR DELETE ON trial_matches
        FOR EACH ROW EXECUTE FUNCTION prevent_trial_match_history_changes();
        """
    )


def downgrade() -> None:
    """Remove lifecycle guards and restore the prior match-run configuration trigger."""
    op.execute("DROP TRIGGER protect_trial_match_history ON trial_matches")
    op.execute("DROP FUNCTION prevent_trial_match_history_changes()")
    op.execute("DROP TRIGGER protect_match_run_configuration ON match_runs")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_match_run_configuration_changes()
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
    op.execute("DROP TRIGGER protect_patient_import_source ON patient_imports")
    op.execute("DROP FUNCTION prevent_patient_import_source_changes()")
    for table_name in IMMUTABLE_SNAPSHOT_TABLES:
        op.execute(f"DROP TRIGGER protect_{table_name}_history ON {table_name}")
    op.execute("DROP FUNCTION prevent_historical_snapshot_changes()")
