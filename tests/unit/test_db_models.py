"""Unit tests for the initial source-linked database schema."""

import app.db.models  # noqa: F401
from app.db.base import Base


def test_models_register_the_required_tables() -> None:
    assert set(Base.metadata.tables) == {
        "criteria",
        "criterion_results",
        "match_runs",
        "patient_facts",
        "patient_imports",
        "patients",
        "review_decisions",
        "trial_matches",
        "trial_versions",
        "trials",
    }


def test_patient_facts_link_to_the_source_import_and_preserve_provenance() -> None:
    patient_facts = Base.metadata.tables["patient_facts"]

    assert {column.name for column in patient_facts.columns} >= {
        "id",
        "patient_id",
        "patient_import_id",
        "kind",
        "code",
        "value",
        "unit",
        "effective_at",
        "provenance",
        "source_resource",
        "normalization",
        "quality_issues",
        "created_at",
    }
    assert {
        foreign_key.target_fullname
        for foreign_key in patient_facts.c.patient_import_id.foreign_keys
    } == {"patient_imports.id"}


def test_trial_versions_cannot_duplicate_a_trial_source_snapshot() -> None:
    trial_versions = Base.metadata.tables["trial_versions"]

    unique_index = next(
        index
        for index in trial_versions.indexes
        if index.name == "uq_trial_versions_nct_id_source_hash"
    )
    assert unique_index.unique
    assert [column.name for column in unique_index.columns] == ["nct_id", "source_hash"]


def test_matching_tables_preserve_the_full_evidence_chain() -> None:
    relationships = {
        "criteria": {"trial_versions.id"},
        "match_runs": {"patient_imports.id"},
        "trial_matches": {"match_runs.id", "trial_versions.id"},
        "criterion_results": {"criteria.id", "trial_matches.id"},
        "review_decisions": {"criterion_results.id"},
    }

    for table_name, expected_targets in relationships.items():
        foreign_key_targets = {
            foreign_key.target_fullname
            for foreign_key in Base.metadata.tables[table_name].foreign_keys
        }
        assert foreign_key_targets == expected_targets


def test_criterion_results_require_evidence_for_non_unknown_outcomes() -> None:
    criterion_results = Base.metadata.tables["criterion_results"]
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in criterion_results.constraints
        if constraint.name is not None and hasattr(constraint, "sqltext")
    }

    assert constraints["ck_criterion_results_outcome"] == (
        "outcome IN ('met', 'not_met', 'unknown', 'conflicting')"
    )
    assert constraints["ck_criterion_results_evidence_required"] == (
        "outcome = 'unknown' OR jsonb_array_length(evidence_fact_ids) > 0"
    )


def test_match_runs_require_queryable_configuration_versions() -> None:
    match_runs = Base.metadata.tables["match_runs"]
    version_fields = {
        "parser_version",
        "retrieval_version",
        "rule_engine_version",
        "terminology_mapping_version",
        "prompt_version",
        "model_configuration_version",
    }

    assert version_fields <= {column.name for column in match_runs.columns}
    assert all(not match_runs.c[field].nullable for field in version_fields)
