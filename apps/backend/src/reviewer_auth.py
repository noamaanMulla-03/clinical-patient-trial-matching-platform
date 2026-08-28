"""Bearer-capability authentication for immutable reviewer corrections."""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.errors import APIError
from src.settings import StartupSettings, load_startup_settings

_reviewer_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="reviewerCorrectionBearer",
    description="Server-configured bearer credential for an identified reviewer.",
)


@dataclass(frozen=True, slots=True)
class ReviewerIdentity:
    """The actor recorded for one authenticated correction."""

    actor_id: str


def authenticate_reviewer_credential(
    credentials: HTTPAuthorizationCredentials | None,
    *,
    settings: StartupSettings,
) -> ReviewerIdentity:
    """Authenticate without exposing supplied credentials in errors or audit data."""
    configured_token = settings.reviewer_correction_token
    configured_actor = settings.reviewer_correction_actor_id
    if configured_token is None or configured_actor is None:
        raise APIError(
            status_code=503,
            code="reviewer_auth.not_configured",
            message=(
                "Reviewer corrections are disabled until reviewer authentication "
                "is configured."
            ),
        )
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not compare_digest(credentials.credentials, configured_token)
    ):
        raise APIError(
            status_code=401,
            code="reviewer_auth.invalid_credential",
            message="A valid reviewer credential is required to append a correction.",
        )
    return ReviewerIdentity(actor_id=configured_actor)


async def authenticated_reviewer(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_reviewer_bearer)
    ],
) -> ReviewerIdentity:
    """FastAPI dependency which binds corrections to server-configured identity."""
    return authenticate_reviewer_credential(
        credentials, settings=load_startup_settings()
    )
