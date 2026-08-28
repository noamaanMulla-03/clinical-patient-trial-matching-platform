"""Conservative source-span eligibility parser checks."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import src.criteria.eligibility_parser as eligibility_parser
from src.criteria.aggregation import _outcome_for
from src.criteria.eligibility_parser import (
    parse_eligibility_text,
    parse_eligibility_text_with_review_metadata,
    raw_parser_output,
    requires_human_review,
)
from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.db.models import Criterion, CriterionResult


def test_parser_emits_exact_atomic_age_rules_from_explicit_sections() -> None:
    eligibility_text = (
        "Inclusion Criteria:\n"
        "- Age 18 years or older\n"
        "- Age 21 to 65 years\n"
        "Exclusion Criteria:\n"
        "- Age at most 16 years\n"
        "- Prior therapy is allowed\n"
    )

    criteria = parse_eligibility_text(eligibility_text)

    assert [criterion.category for criterion in criteria] == [
        "inclusion",
        "inclusion",
        "inclusion",
        "exclusion",
    ]
    assert [criterion.rule.model_dump() for criterion in criteria] == [
        {"kind": "age", "operator": "at_least", "years": 18},
        {"kind": "age", "operator": "at_least", "years": 21},
        {"kind": "age", "operator": "at_most", "years": 65},
        {"kind": "age", "operator": "at_most", "years": 16},
    ]
    assert [criterion.source_text for criterion in criteria] == [
        "Age 18 years or older",
        "Age 21 to 65 years",
        "Age 21 to 65 years",
        "Age at most 16 years",
    ]
    assert all(
        eligibility_text[criterion.source_start : criterion.source_end]
        == criterion.source_text
        for criterion in criteria
    )


def test_parser_abstains_for_unheaded_or_unsupported_eligibility_text() -> None:
    assert parse_eligibility_text("Adults age 18 years or older.") == ()
    assert (
        parse_eligibility_text("Inclusion Criteria:\n- ECOG performance status 0-1")
        == ()
    )
    assert parse_eligibility_text(None) == ()


def test_parser_marks_ambiguous_nested_and_low_confidence_source_context() -> None:
    eligibility_text = (
        "Inclusion Criteria:\n"
        "- Age 18 years or older (unless the investigator approves an exception)\n"
        "- Age >= 21\n"
    )

    parsed = parse_eligibility_text_with_review_metadata(eligibility_text)

    assert [item.criterion.source_text for item in parsed] == [
        "Age 18 years or older",
        "Age >= 21",
    ]
    assert parsed[0].review_reasons == ("ambiguous_clause", "nested_clause")
    assert parsed[0].parser_confidence == Decimal("0.7500")
    assert parsed[1].review_reasons == ("low_confidence_parse",)
    assert parsed[1].parser_confidence == Decimal("0.6000")


def test_raw_parser_output_preserves_the_versioned_structured_parser_response() -> None:
    parsed = parse_eligibility_text_with_review_metadata(
        "Inclusion Criteria:\n- Age 18 years or older"
    )

    assert raw_parser_output(parsed) == {
        "schema_version": ELIGIBILITY_PARSER_CONFIGURATION.output_schema_version,
        "criteria": [
            {
                "category": "inclusion",
                "source_text": "Age 18 years or older",
                "source_start": 22,
                "source_end": 43,
                "rule": {"kind": "age", "operator": "at_least", "years": 18},
                "parser_confidence": "1.0000",
                "review_reasons": [],
            }
        ],
    }


def test_parser_automation_gate_remains_disabled_after_parser_evaluation() -> None:
    """Synthetic parser checks cannot independently authorize automated matching."""
    assert ELIGIBILITY_PARSER_CONFIGURATION.automated_criterion_use_enabled is False


def test_ambiguous_criterion_abstains_from_an_automated_match_outcome() -> None:
    parsed = parse_eligibility_text_with_review_metadata(
        "Inclusion Criteria:\n- Age 18 years or older (unless an exception applies)"
    )
    stored_criterion = Criterion(
        id=uuid4(),
        category=parsed[0].criterion.category,
        requires_human_review=bool(parsed[0].review_reasons),
    )
    resolved_result = CriterionResult(
        id=uuid4(),
        criterion_id=stored_criterion.id,
        outcome="met",
        requires_review=False,
    )

    assert parsed[0].review_reasons == ("ambiguous_clause", "nested_clause")
    assert _outcome_for([stored_criterion], [resolved_result]) == "needs_review"


def test_ambiguous_criterion_requires_review_even_if_automation_is_enabled(
    monkeypatch,
) -> None:
    parsed = parse_eligibility_text_with_review_metadata(
        "Inclusion Criteria:\n- Age 18 years or older (unless an exception applies)"
    )
    monkeypatch.setattr(
        eligibility_parser,
        "ELIGIBILITY_PARSER_CONFIGURATION",
        replace(ELIGIBILITY_PARSER_CONFIGURATION, automated_criterion_use_enabled=True),
    )

    assert requires_human_review(parsed[0]) is True
