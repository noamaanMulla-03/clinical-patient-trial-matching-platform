"""PostgreSQL lexical candidate selection over indexed current trial fields."""

from __future__ import annotations

from sqlalchemy import Select, cast, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG

from app.db.models import Trial
from app.retrieval.schemas import PatientDerivedRetrievalQuery

_SIMPLE_CONFIG = cast(literal("simple"), REGCONFIG)
_STRING_JSON_FILTER = cast(literal('["string"]'), JSONB)


def lexical_trial_candidates_statement(
    query: PatientDerivedRetrievalQuery, *, candidate_limit: int
) -> Select[tuple[Trial]]:
    """Select current trials matching one non-empty patient-derived lexical query."""
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")

    if not query.lexical_text:
        # An uncertain patient snapshot must not turn into an unbounded catalogue scan.
        return select(Trial).where(false())
    tsquery = func.websearch_to_tsquery(_SIMPLE_CONFIG, query.lexical_text)
    documents = (
        func.to_tsvector(_SIMPLE_CONFIG, func.coalesce(Trial.title, "")),
        func.jsonb_to_tsvector(_SIMPLE_CONFIG, Trial.conditions, _STRING_JSON_FILTER),
        func.jsonb_to_tsvector(
            _SIMPLE_CONFIG, Trial.interventions, _STRING_JSON_FILTER
        ),
        func.to_tsvector(_SIMPLE_CONFIG, func.coalesce(Trial.eligibility_text, "")),
    )
    return (
        select(Trial)
        .where(or_(*(document.op("@@")(tsquery) for document in documents)))
        .order_by(Trial.nct_id)
        .limit(candidate_limit)
    )
