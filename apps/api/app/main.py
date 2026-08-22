"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.errors import install_api_error_handlers, request_id_middleware
from app.observability.redaction import configure_log_redaction
from app.routes.patients import router as patients_router
from app.routes.system import router as system_router
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
    openapi_url="/openapi.json",
    docs_url="/docs",
    openapi_tags=[
        {
            "name": "patients",
            "description": (
                "Synthetic FHIR patient imports and normalized patient data."
            ),
        },
        {
            "name": "system",
            "description": (
                "Operational API routes that do not process clinical content."
            ),
        },
    ],
    lifespan=lifespan,
)
app.middleware("http")(request_id_middleware)
install_api_error_handlers(app)
app.include_router(system_router)
app.include_router(patients_router)
