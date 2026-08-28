"""Conservative deterministic parsing of explicit age clauses in trial criteria."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.criteria.parser_config import ELIGIBILITY_PARSER_CONFIGURATION
from src.criteria.schemas import (
    AtomicCriterion,
    CriterionCategory,
    CriterionReviewReason,
)
from src.db.models import Criterion, TrialParserRun, TrialVersion

ELIGIBILITY_PARSER_VERSION = ELIGIBILITY_PARSER_CONFIGURATION.parser_version

_SECTION_HEADER_PATTERN = re.compile(
    r"(?im)^[ \t]*(?P<category>inclusion|exclusion) criteria:?[ \t]*$"
)
_AGE_RANGE_PATTERN = re.compile(
    r"(?i)\bage\s*(?:of\s*)?(?P<minimum>\d{1,3})\s*(?:-|to)\s*"
    r"(?P<maximum>\d{1,3})\s*years?\b"
)
_MINIMUM_AGE_PATTERNS = (
    re.compile(
        r"(?i)\bage\s*(?:of\s*)?(?P<years>\d{1,3})\s*years?\s*"
        r"(?:or|and) older\b"
    ),
    re.compile(
        r"(?i)\b(?:age\s*)?(?:at least|minimum age of)\s*"
        r"(?P<years>\d{1,3})\s*years?\b"
    ),
    re.compile(r"(?i)\bage\s*(?:>=|≥)\s*(?P<years>\d{1,3})\b"),
)
_MAXIMUM_AGE_PATTERNS = (
    re.compile(
        r"(?i)\bage\s*(?P<years>\d{1,3})\s*years?\s*"
        r"(?:or|and) younger\b"
    ),
    re.compile(
        r"(?i)\b(?:age\s*)?(?:at most|maximum age of|no more than)\s*"
        r"(?P<years>\d{1,3})\s*years?\b"
    ),
    re.compile(r"(?i)\bage\s*(?:<=|≤)\s*(?P<years>\d{1,3})\b"),
)
_AMBIGUOUS_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(?:and/or|unless|except|either|otherwise)\b"
)
_SYMBOLIC_AGE_PATTERN = re.compile(r"(?i)\bage\s*(?:>=|≥|<=|≤)\s*\d")


@dataclass(frozen=True, slots=True)
class ParsedCriterion:
    """One deterministic rule plus its review metadata from exact source context."""

    criterion: AtomicCriterion
    parser_confidence: Decimal
    review_reasons: tuple[CriterionReviewReason, ...]


class EligibilityParserError(ValueError):
    """Raised when parsed criteria cannot be tied to one exact trial snapshot."""


def parse_eligibility_text(eligibility_text: str | None) -> tuple[AtomicCriterion, ...]:
    """Return only unambiguous age criteria with exact source spans.

    A missing section heading, unsupported wording, or any other eligibility text
    yields no criterion instead of inventing a rule. Parsed records are retained
    for review and are not an autonomous eligibility determination.
    """
    if eligibility_text is None:
        return ()
    criteria_by_source_position: list[tuple[int, int, AtomicCriterion]] = []
    parse_order = 0
    for category, section_start, section_end in _criterion_sections(eligibility_text):
        section_text = eligibility_text[section_start:section_end]
        occupied_spans: list[tuple[int, int]] = []
        for match in _AGE_RANGE_PATTERN.finditer(section_text):
            start, end = _absolute_span(match, section_start)
            years = int(match["minimum"])
            maximum_years = int(match["maximum"])
            if years > maximum_years:
                continue
            occupied_spans.append((start, end))
            criteria_by_source_position.append(
                (
                    start,
                    parse_order,
                    _age_criterion(
                        eligibility_text,
                        category=category,
                        start=start,
                        end=end,
                        operator="at_least",
                        years=years,
                    ),
                )
            )
            parse_order += 1
            criteria_by_source_position.append(
                (
                    start,
                    parse_order,
                    _age_criterion(
                        eligibility_text,
                        category=category,
                        start=start,
                        end=end,
                        operator="at_most",
                        years=maximum_years,
                    ),
                )
            )
            parse_order += 1
        for criterion in _single_bound_age_criteria(
            eligibility_text,
            category=category,
            section_text=section_text,
            section_start=section_start,
            occupied_spans=occupied_spans,
            patterns=_MINIMUM_AGE_PATTERNS,
            operator="at_least",
        ):
            criteria_by_source_position.append(
                (criterion.source_start, parse_order, criterion)
            )
            parse_order += 1
        for criterion in _single_bound_age_criteria(
            eligibility_text,
            category=category,
            section_text=section_text,
            section_start=section_start,
            occupied_spans=occupied_spans,
            patterns=_MAXIMUM_AGE_PATTERNS,
            operator="at_most",
        ):
            criteria_by_source_position.append(
                (criterion.source_start, parse_order, criterion)
            )
            parse_order += 1
    return tuple(
        criterion
        for _, _, criterion in sorted(
            criteria_by_source_position, key=lambda item: item[:2]
        )
    )


def parse_eligibility_text_with_review_metadata(
    eligibility_text: str | None,
) -> tuple[ParsedCriterion, ...]:
    """Attach explicit review signals without changing the immutable source rule."""
    if eligibility_text is None:
        return ()
    return tuple(
        _parsed_criterion_with_review_metadata(eligibility_text, criterion)
        for criterion in parse_eligibility_text(eligibility_text)
    )


def raw_parser_output(
    parsed_criteria: tuple[ParsedCriterion, ...],
) -> dict[str, object]:
    """Serialize the deterministic parser response before database persistence.

    This is provenance for public trial text only. It is not application logging,
    does not contain patient data, and intentionally has no prompt or model output.
    """
    return {
        "schema_version": ELIGIBILITY_PARSER_CONFIGURATION.output_schema_version,
        "criteria": [
            {
                "category": parsed.criterion.category,
                "source_text": parsed.criterion.source_text,
                "source_start": parsed.criterion.source_start,
                "source_end": parsed.criterion.source_end,
                "rule": parsed.criterion.rule.model_dump(mode="json"),
                "parser_confidence": str(parsed.parser_confidence),
                "review_reasons": list(parsed.review_reasons),
            }
            for parsed in parsed_criteria
        ],
    }


async def create_parsed_criteria(
    session: AsyncSession,
    *,
    trial_version_id: UUID,
    eligibility_text: str | None,
) -> tuple[Criterion, ...]:
    """Persist exact parsed clauses as review-required trial-version evidence."""
    trial_version = await session.get(TrialVersion, trial_version_id)
    if trial_version is None:
        raise EligibilityParserError("Trial version was not found for parsed criteria.")
    existing = await session.scalar(
        select(Criterion.id)
        .where(Criterion.trial_version_id == trial_version_id)
        .limit(1)
    )
    if existing is not None:
        raise EligibilityParserError(
            "Trial version already has criteria and cannot be parsed again."
        )
    existing_run = await session.scalar(
        select(TrialParserRun.id)
        .where(TrialParserRun.trial_version_id == trial_version_id)
        .limit(1)
    )
    if existing_run is not None:
        raise EligibilityParserError(
            "Trial version already has parser provenance and cannot be parsed again."
        )
    parsed_criteria = parse_eligibility_text_with_review_metadata(eligibility_text)
    session.add(
        TrialParserRun(
            id=uuid4(),
            trial_version_id=trial_version_id,
            parser_version=ELIGIBILITY_PARSER_CONFIGURATION.parser_version,
            prompt_version=ELIGIBILITY_PARSER_CONFIGURATION.prompt_version,
            model_configuration_version=(
                ELIGIBILITY_PARSER_CONFIGURATION.model_configuration_version
            ),
            raw_output=raw_parser_output(parsed_criteria),
        )
    )
    records: list[Criterion] = []
    for parsed_criterion in parsed_criteria:
        criterion = parsed_criterion.criterion
        _require_exact_source_span(eligibility_text, criterion)
        record = Criterion(
            id=uuid4(),
            trial_version_id=trial_version_id,
            category=criterion.category,
            source_text=criterion.source_text,
            source_start=criterion.source_start,
            source_end=criterion.source_end,
            parsed_data=criterion.rule.model_dump(mode="json"),
            parser_version=ELIGIBILITY_PARSER_VERSION,
            parser_confidence=parsed_criterion.parser_confidence,
            # Generated rules cannot affect an automated match outcome until the
            # explicit parser acceptance gate is reviewed and enabled in code.
            requires_human_review=requires_human_review(parsed_criterion),
            review_reasons=list(parsed_criterion.review_reasons),
        )
        session.add(record)
        records.append(record)
    await session.flush()
    return tuple(records)


def requires_human_review(parsed_criterion: ParsedCriterion) -> bool:
    """Keep ambiguity review-only even after any future automation enablement."""
    return (
        bool(parsed_criterion.review_reasons)
        or not ELIGIBILITY_PARSER_CONFIGURATION.automated_criterion_use_enabled
    )


def _parsed_criterion_with_review_metadata(
    eligibility_text: str,
    criterion: AtomicCriterion,
) -> ParsedCriterion:
    source_line = _source_line_for_span(
        eligibility_text, criterion.source_start, criterion.source_end
    )
    relative_start = criterion.source_start - source_line.start
    relative_end = criterion.source_end - source_line.start
    surrounding_text = (
        source_line.text[:relative_start] + source_line.text[relative_end:]
    )
    review_reasons: list[CriterionReviewReason] = []
    if _AMBIGUOUS_CONTEXT_PATTERN.search(surrounding_text):
        review_reasons.append("ambiguous_clause")
    if "(" in source_line.text or ")" in source_line.text:
        review_reasons.append("nested_clause")
    if _SYMBOLIC_AGE_PATTERN.search(criterion.source_text):
        review_reasons.append("low_confidence_parse")
    confidence = (
        Decimal("0.6000")
        if "low_confidence_parse" in review_reasons
        else Decimal("0.7500")
        if review_reasons
        else Decimal("1.0000")
    )
    return ParsedCriterion(
        criterion=criterion,
        parser_confidence=confidence,
        review_reasons=tuple(review_reasons),
    )


@dataclass(frozen=True, slots=True)
class _SourceLine:
    start: int
    text: str


def _source_line_for_span(eligibility_text: str, start: int, end: int) -> _SourceLine:
    line_start = eligibility_text.rfind("\n", 0, start) + 1
    line_end = eligibility_text.find("\n", end)
    if line_end == -1:
        line_end = len(eligibility_text)
    return _SourceLine(start=line_start, text=eligibility_text[line_start:line_end])


def _criterion_sections(
    eligibility_text: str,
) -> Iterator[tuple[CriterionCategory, int, int]]:
    headers = list(_SECTION_HEADER_PATTERN.finditer(eligibility_text))
    for index, header in enumerate(headers):
        category = cast(CriterionCategory, header["category"].lower())
        start = header.end()
        if start < len(eligibility_text) and eligibility_text[start] == "\n":
            start += 1
        end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(eligibility_text)
        )
        yield category, start, end


def _single_bound_age_criteria(
    eligibility_text: str,
    *,
    category: CriterionCategory,
    section_text: str,
    section_start: int,
    occupied_spans: list[tuple[int, int]],
    patterns: tuple[re.Pattern[str], ...],
    operator: str,
) -> Iterator[AtomicCriterion]:
    for pattern in patterns:
        for match in pattern.finditer(section_text):
            start, end = _absolute_span(match, section_start)
            if _overlaps_known_span(start, end, occupied_spans):
                continue
            yield _age_criterion(
                eligibility_text,
                category=category,
                start=start,
                end=end,
                operator=operator,
                years=int(match["years"]),
            )


def _age_criterion(
    eligibility_text: str,
    *,
    category: CriterionCategory,
    start: int,
    end: int,
    operator: str,
    years: int,
) -> AtomicCriterion:
    return AtomicCriterion.model_validate(
        {
            "category": category,
            "source_text": eligibility_text[start:end],
            "source_start": start,
            "source_end": end,
            "rule": {"kind": "age", "operator": operator, "years": years},
        }
    )


def _absolute_span(match: re.Match[str], section_start: int) -> tuple[int, int]:
    return section_start + match.start(), section_start + match.end()


def _overlaps_known_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(
        start < known_end and end > known_start for known_start, known_end in spans
    )


def _require_exact_source_span(
    eligibility_text: str | None, criterion: AtomicCriterion
) -> None:
    if (
        eligibility_text is None
        or criterion.source_end > len(eligibility_text)
        or eligibility_text[criterion.source_start : criterion.source_end]
        != criterion.source_text
    ):
        raise EligibilityParserError(
            "Parsed criterion source span does not match trial eligibility text."
        )
