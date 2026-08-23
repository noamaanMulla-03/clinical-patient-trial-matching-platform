"""Unit tests for API startup safety rules."""

import pytest

from app.settings import StartupSafetyError, validate_startup_settings


def test_development_is_allowed_by_default() -> None:
    settings = validate_startup_settings({})

    assert settings.app_env == "development"
    assert not settings.allow_real_patient_data


def test_production_like_environment_is_blocked_by_default() -> None:
    with pytest.raises(StartupSafetyError, match="Production-like startup is blocked"):
        validate_startup_settings({"APP_ENV": "production"})


def test_explicit_override_allows_production_like_non_clinical_startup() -> None:
    settings = validate_startup_settings(
        {
            "APP_ENV": "staging",
            "ALLOW_PRODUCTION_LIKE_ENVIRONMENT": "true",
        }
    )

    assert settings.app_env == "staging"


def test_real_patient_data_mode_is_always_blocked() -> None:
    with pytest.raises(StartupSafetyError, match="research-only build"):
        validate_startup_settings({"ALLOW_REAL_PATIENT_DATA": "true"})


def test_invalid_boolean_is_rejected() -> None:
    with pytest.raises(StartupSafetyError, match="ALLOW_REAL_PATIENT_DATA must be"):
        validate_startup_settings({"ALLOW_REAL_PATIENT_DATA": "perhaps"})
