"""PostgreSQL lexical candidate selection over indexed current trial fields."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, cast, false, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG

from src.db.models import TrialVersion
from src.retrieval.schemas import PatientDerivedRetrievalQuery

_SIMPLE_CONFIG = cast(literal("simple"), REGCONFIG)
_STRING_JSON_FILTER = cast(literal('["string"]'), JSONB)


def lexical_trial_candidates_statement(
    query: PatientDerivedRetrievalQuery,
    *,
    candidate_limit: int,
    catalogue_as_of: datetime,
) -> Select[tuple[TrialVersion]]:
    """Select the catalogue that existed at one fixed run timestamp."""
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive.")

    if not query.lexical_text:
        # An uncertain patient snapshot must not turn into an unbounded catalogue scan.
        return select(TrialVersion).where(false())
    tsquery = func.websearch_to_tsquery(_SIMPLE_CONFIG, query.lexical_text)
    document = func.jsonb_to_tsvector(
        _SIMPLE_CONFIG, TrialVersion.raw_study, _STRING_JSON_FILTER
    )
    # Rank the entire matching relation before applying the bounded retrieval-pool
    # limit. Ordering by NCT ID first would silently discard better candidates.
    rank = func.ts_rank_cd(document, tsquery)
    return (
        select(TrialVersion)
        .where(
            TrialVersion.ingested_at <= catalogue_as_of,
            (TrialVersion.superseded_at.is_(None))
            | (TrialVersion.superseded_at > catalogue_as_of),
            document.op("@@")(tsquery),
        )
        .order_by(rank.desc(), TrialVersion.nct_id)
        .limit(candidate_limit)
    )
