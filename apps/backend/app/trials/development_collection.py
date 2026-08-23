"""A deliberately small, fixed ClinicalTrials.gov collection for development."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TrialSync
from app.workers.trial_ingestion import (
    TrialIngestionRequest,
    create_queued_trial_sync,
)


@dataclass(frozen=True, slots=True)
class DevelopmentTrialCollection:
    """Named NCT selection whose membership changes only through source control."""

    collection_id: str
    nct_ids: tuple[str, ...]


# These identifiers were selected from ClinicalTrials.gov on 2026-08-23. Keeping
# them fixed prevents development behavior from depending on a changing search page.
DEVELOPMENT_TRIAL_COLLECTION = DevelopmentTrialCollection(
    collection_id="development-melanoma-v1",
    nct_ids=("NCT02434107", "NCT01610531", "NCT00849407"),
)


def development_trial_ingestion_requests() -> tuple[TrialIngestionRequest, ...]:
    """Create the exact bounded NCT requests used for local development syncs."""
    return tuple(
        TrialIngestionRequest(
            nct_id=nct_id,
            collection_id=DEVELOPMENT_TRIAL_COLLECTION.collection_id,
        )
        for nct_id in DEVELOPMENT_TRIAL_COLLECTION.nct_ids
    )


async def queue_development_trial_collection(
    session: AsyncSession,
) -> tuple[TrialSync, ...]:
    """Persist one queued job per fixed trial without making remote calls inline."""
    return tuple(
        [
            await create_queued_trial_sync(session, request)
            for request in development_trial_ingestion_requests()
        ]
    )
