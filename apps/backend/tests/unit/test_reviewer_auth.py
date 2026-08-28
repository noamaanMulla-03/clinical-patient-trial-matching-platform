"""Reviewer correction authentication must derive the actor server-side."""

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from src.errors import APIError
from src.reviewer_auth import authenticate_reviewer_credential
from src.settings import load_startup_settings


def _settings():
    return load_startup_settings(
        {
            "REVIEWER_CORRECTION_TOKEN": "reviewer-secret",
            "REVIEWER_CORRECTION_ACTOR_ID": "reviewer-01",
        }
    )


def test_reviewer_identity_is_derived_from_server_configuration() -> None:
    identity = authenticate_reviewer_credential(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="reviewer-secret"),
        settings=_settings(),
    )

    assert identity.actor_id == "reviewer-01"


@pytest.mark.parametrize(
    "credentials",
    [
        None,
        HTTPAuthorizationCredentials(scheme="Basic", credentials="reviewer-secret"),
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-secret"),
    ],
)
def test_reviewer_identity_rejects_missing_or_invalid_credentials(credentials) -> None:
    with pytest.raises(APIError) as error:
        authenticate_reviewer_credential(credentials, settings=_settings())

    assert error.value.status_code == 401


def test_reviewer_identity_disables_corrections_without_server_configuration() -> None:
    with pytest.raises(APIError) as error:
        authenticate_reviewer_credential(None, settings=load_startup_settings({}))

    assert error.value.status_code == 503
