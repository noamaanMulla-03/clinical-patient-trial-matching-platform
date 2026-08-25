"""Small durable-job dispatcher for the local synthetic-data Compose stack."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MatchRun, TrialEmbeddingJob, TrialSync
from app.db.session import session_factory_for
from app.services.trial_embeddings import queue_next_missing_trial_embedding_job
from app.settings import validate_startup_settings
from app.workers.match_runs import run_match_run_job
from app.workers.trial_embeddings import run_queued_trial_embedding_job
from app.workers.trial_ingestion import run_queued_trial_ingestion_job

DEFAULT_POLL_SECONDS = 1.0


def worker_poll_seconds(environ: dict[str, str] | None = None) -> float:
    """Read a bounded idle delay without accepting invalid worker configuration."""
    value = (environ or os.environ).get(
        "WORKER_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)
    )
    try:
        seconds = float(value)
    except ValueError as error:
        raise ValueError("WORKER_POLL_SECONDS must be a positive number.") from error
    if seconds <= 0 or seconds > 60:
        raise ValueError("WORKER_POLL_SECONDS must be greater than 0 and at most 60.")
    return seconds


async def process_next_job(session: AsyncSession) -> bool:
    """Claim at most one durable job so runs remain independently traceable."""
    sync_id = await session.scalar(
        select(TrialSync.id)
        .where(TrialSync.status == "queued")
        .order_by(TrialSync.created_at, TrialSync.id)
        .limit(1)
    )
    if sync_id is not None:
        await run_queued_trial_ingestion_job(session, sync_id)
        return True

    match_run_id = await session.scalar(
        select(MatchRun.id)
        .where(MatchRun.status == "queued")
        .order_by(MatchRun.created_at, MatchRun.id)
        .limit(1)
    )
    if match_run_id is None:
        embedding_job_id = await session.scalar(
            select(TrialEmbeddingJob.id)
            .where(TrialEmbeddingJob.status == "queued")
            .order_by(TrialEmbeddingJob.created_at, TrialEmbeddingJob.id)
            .limit(1)
        )
        if embedding_job_id is None:
            backfill_job = await queue_next_missing_trial_embedding_job(session)
            if backfill_job is None:
                return False
            embedding_job_id = backfill_job.id
        await run_queued_trial_embedding_job(session, embedding_job_id)
        return True
    await run_match_run_job(session, match_run_id)
    return True


async def run_dispatcher() -> None:
    """Continuously dispatch queued public-trial and synthetic-patient jobs."""
    validate_startup_settings()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for the worker.")
    session_factory = session_factory_for(database_url)
    poll_seconds = worker_poll_seconds()
    while True:
        async with session_factory() as session, session.begin():
            processed = await process_next_job(session)
        if not processed:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    """Provide a module entry point for the Compose worker service."""
    asyncio.run(run_dispatcher())


if __name__ == "__main__":
    main()
