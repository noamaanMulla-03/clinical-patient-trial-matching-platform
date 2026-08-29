"""Read-only retrieval adapter that reuses the application candidate pipeline.

TREC topics are public benchmark text, never FHIR bundles or patient records.
This module converts only their tokens into transient retrieval terms so an
isolated, public-trial PostgreSQL catalogue can exercise the same SQL, vector,
fusion, and final-ordering code used by a match run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TrialVersion
from src.retrieval.embedding_encoder import EmbeddingEncoder, EmbeddingEncoderError
from src.retrieval.filtering import metadata_from_trial, trial_matches_metadata
from src.retrieval.fusion import fuse_ranked_trial_candidates
from src.retrieval.lexical import lexical_trial_candidates_statement
from src.retrieval.reranking import rerank_fused_trial_candidates
from src.retrieval.schemas import PatientDerivedRetrievalQuery
from src.retrieval.scoring import rank_scored_trials, score_trial_candidate
from src.retrieval.semantic import (
    SemanticRetrievalIncompleteError,
    semantic_trial_candidates,
)
from src.retrieval.trial_documents import document_from_trial_version
from src.services.match_runs import (
    FUSION_POOL_LIMIT,
    LEXICAL_POOL_LIMIT,
    MAX_MATCH_RUN_CANDIDATES,
    SEMANTIC_POOL_LIMIT,
)


@dataclass(frozen=True, slots=True)
class ApplicationPathCandidates:
    """Public identifiers returned by each actual candidate-ranking stage."""

    lexical_nct_ids: list[str]
    semantic_nct_ids: list[str]
    hybrid_nct_ids: list[str]
    final_nct_ids: list[str]
    mode: str


async def retrieve_application_path_candidates(
    session: AsyncSession,
    query: PatientDerivedRetrievalQuery,
    *,
    catalogue_as_of: datetime,
    encoder: EmbeddingEncoder | None = None,
) -> ApplicationPathCandidates:
    """Run the exact live candidate retrieval components without persistence.

    This deliberately excludes import, criterion evaluation, and reviewer state:
    those are not part of a TREC retrieval benchmark.  It also does not create
    match runs, patient imports, or patient facts.
    """
    lexical_versions = list(
        await session.scalars(
            lexical_trial_candidates_statement(
                query,
                candidate_limit=LEXICAL_POOL_LIMIT,
                catalogue_as_of=catalogue_as_of,
            )
        )
    )
    lexical_trials = [
        document_from_trial_version(version) for version in lexical_versions
    ]
    ranked_lexical = rank_scored_trials(
        (trial, score)
        for trial in lexical_trials
        if trial_matches_metadata(metadata_from_trial(trial), query.filters)
        and (score := score_trial_candidate(trial, query)) is not None
    )

    try:
        semantic = await semantic_trial_candidates(
            session,
            query,
            candidate_limit=SEMANTIC_POOL_LIMIT,
            catalogue_as_of=catalogue_as_of,
            encoder=encoder,
        )
    except SemanticRetrievalIncompleteError:
        semantic = ()
        mode = "lexical_only_partial_semantic_catalogue"
    except EmbeddingEncoderError:
        semantic = ()
        mode = "lexical_only_model_unavailable"
    else:
        mode = "hybrid" if semantic else "lexical_only_no_semantic_candidates"

    filtered_semantic = tuple(
        candidate
        for candidate in semantic
        if trial_matches_metadata(metadata_from_trial(candidate.trial), query.filters)
    )
    fused = fuse_ranked_trial_candidates(
        ranked_lexical, filtered_semantic, candidate_limit=FUSION_POOL_LIMIT
    )
    final = rerank_fused_trial_candidates(
        fused, query, candidate_limit=MAX_MATCH_RUN_CANDIDATES
    )
    return ApplicationPathCandidates(
        lexical_nct_ids=[trial.nct_id for trial, _ in ranked_lexical],
        semantic_nct_ids=[candidate.trial.nct_id for candidate in filtered_semantic],
        hybrid_nct_ids=[trial.nct_id for trial, _ in fused],
        final_nct_ids=[trial.nct_id for trial, _ in final],
        mode=mode,
    )


async def current_catalogue_trial_count(
    session: AsyncSession, *, catalogue_as_of: datetime
) -> int:
    """Count the immutable public-trial catalogue selected by one evaluation."""
    count = await session.scalar(
        select(func.count())
        .select_from(TrialVersion)
        .where(
            TrialVersion.ingested_at <= catalogue_as_of,
            (TrialVersion.superseded_at.is_(None))
            | (TrialVersion.superseded_at > catalogue_as_of),
        )
    )
    return int(count or 0)
