"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.observability.redaction import configure_log_redaction
from app.settings import validate_startup_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Run safety checks before the API accepts any request."""
    configure_log_redaction()
    validate_startup_settings()
    yield


app = FastAPI(
    title="Clinical Trial Patient-Matching Platform",
    description="Research-only decision support using synthetic data.",
    version="0.1.0",
    lifespan=lifespan,
)
