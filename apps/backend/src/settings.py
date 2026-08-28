"""Environment settings and startup safety checks for the API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "test"})
PRODUCTION_LIKE_ENVIRONMENTS = frozenset({"production", "prod", "staging", "stage"})


class StartupSafetyError(RuntimeError):
    """Raised when the API would start in an unsafe or unsupported mode."""


@dataclass(frozen=True, slots=True)
class StartupSettings:
    """The small, explicit set of settings required before API startup."""

    app_env: str
    allow_production_like_environment: bool
    allow_real_patient_data: bool
    reviewer_correction_token: str | None
    reviewer_correction_actor_id: str | None


def _read_bool(environ: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default

    normalized_value = value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False

    raise StartupSafetyError(
        f"{name} must be one of true/false, yes/no, on/off, or 1/0."
    )


def _read_optional_text(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def load_startup_settings(
    environ: Mapping[str, str] | None = None,
) -> StartupSettings:
    """Read and normalize only settings needed before API startup."""
    values = os.environ if environ is None else environ
    app_env = values.get("APP_ENV", "development").strip().lower()

    if not app_env:
        raise StartupSafetyError("APP_ENV must not be blank.")

    reviewer_correction_token = _read_optional_text(values, "REVIEWER_CORRECTION_TOKEN")
    reviewer_correction_actor_id = _read_optional_text(
        values, "REVIEWER_CORRECTION_ACTOR_ID"
    )
    if (reviewer_correction_token is None) != (reviewer_correction_actor_id is None):
        raise StartupSafetyError(
            "REVIEWER_CORRECTION_TOKEN and REVIEWER_CORRECTION_ACTOR_ID must be "
            "set together."
        )

    return StartupSettings(
        app_env=app_env,
        allow_production_like_environment=_read_bool(
            values, "ALLOW_PRODUCTION_LIKE_ENVIRONMENT", default=False
        ),
        allow_real_patient_data=_read_bool(
            values, "ALLOW_REAL_PATIENT_DATA", default=False
        ),
        reviewer_correction_token=reviewer_correction_token,
        reviewer_correction_actor_id=reviewer_correction_actor_id,
    )


def validate_startup_settings(
    environ: Mapping[str, str] | None = None,
) -> StartupSettings:
    """Refuse unsafe modes before the API can receive requests."""
    settings = load_startup_settings(environ)

    if settings.allow_real_patient_data:
        raise StartupSafetyError(
            "ALLOW_REAL_PATIENT_DATA=true is not supported by this research-only build."
        )

    if (
        settings.app_env in PRODUCTION_LIKE_ENVIRONMENTS
        and not settings.allow_production_like_environment
    ):
        raise StartupSafetyError(
            "Production-like startup is blocked. Set "
            "ALLOW_PRODUCTION_LIKE_ENVIRONMENT=true only for an explicitly "
            "reviewed non-clinical deployment."
        )

    if (
        settings.app_env not in DEVELOPMENT_ENVIRONMENTS
        and settings.app_env not in PRODUCTION_LIKE_ENVIRONMENTS
    ):
        raise StartupSafetyError(
            f"APP_ENV={settings.app_env!r} is unsupported. Use development, test, "
            "staging, or production."
        )

    return settings
