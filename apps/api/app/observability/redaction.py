"""Conservative clinical-content redaction for application logging."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED_VALUE = "[REDACTED]"
REDACTED_LOG_ARGUMENTS = "[REDACTED: dynamic log arguments omitted]"
REDACTED_FHIR_CONTENT = "[REDACTED: FHIR content]"
_REDACTED_ACCESS_LOG_ARGS = (REDACTED_VALUE, "REQUEST", REDACTED_VALUE, "1.1", 0)

SENSITIVE_FIELD_TOKENS = frozenset(
    {
        "address",
        "allergy",
        "birth",
        "bundle",
        "clinical",
        "condition",
        "diagnosis",
        "dob",
        "email",
        "evidence",
        "fhir",
        "lab",
        "laboratory",
        "medication",
        "medical",
        "model_input",
        "model_output",
        "mrn",
        "name",
        "note",
        "observation",
        "patient",
        "payload",
        "phone",
        "procedure",
        "prompt",
        "raw",
        "request_body",
        "resource",
        "response_body",
        "ssn",
        "symptom",
    }
)

EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,3}\)?[-.\s]?)?\d{3}[-.\s]\d{4}(?!\w)"
)
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
FHIR_CONTENT_PATTERN = re.compile(
    r"(?i)([\"']resourceType[\"']\s*:|<\s*(?:Bundle|Patient|Observation|Condition)\b)"
)

STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _is_sensitive_field_name(name: str) -> bool:
    normalized_name = name.strip().lower().replace("-", "_")
    return any(
        token == normalized_name or token in normalized_name.split("_")
        for token in SENSITIVE_FIELD_TOKENS
    )


def redact_text(value: str) -> str:
    """Remove common direct identifiers and obvious serialized FHIR content."""
    if FHIR_CONTENT_PATTERN.search(value):
        return REDACTED_FHIR_CONTENT

    redacted_value = EMAIL_PATTERN.sub(REDACTED_VALUE, value)
    redacted_value = PHONE_PATTERN.sub(REDACTED_VALUE, redacted_value)
    return SSN_PATTERN.sub(REDACTED_VALUE, redacted_value)


def redact_log_value(value: Any, *, field_name: str | None = None) -> Any:
    """Recursively remove clinical values from structured log context."""
    if field_name is not None and _is_sensitive_field_name(field_name):
        return REDACTED_VALUE

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): redact_log_value(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, Sequence):
        return [redact_log_value(item) for item in value]
    return value


def redact_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return a safe copy of structured fields before they reach a logger."""
    return {
        field_name: redact_log_value(value, field_name=field_name)
        for field_name, value in fields.items()
    }


class ClinicalContentRedactionFilter(logging.Filter):
    """Redact structured extras and dynamic arguments without breaking Uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if record.name == "uvicorn.access":
                # Uvicorn's AccessFormatter must unpack five values from args.
                # Preserve that shape while removing the client address, method,
                # URL, protocol, and status from the emitted log line.
                record.args = _REDACTED_ACCESS_LOG_ARGS
            else:
                # Dynamic arguments may contain arbitrary clinical text. Preserve
                # only the static message template instead of classifying values.
                record.msg = f"{record.msg} {REDACTED_LOG_ARGUMENTS}"
                record.args = ()
        elif isinstance(record.msg, str):
            record.msg = redact_text(record.msg)

        for field_name, value in list(record.__dict__.items()):
            if field_name not in STANDARD_LOG_RECORD_FIELDS:
                record.__dict__[field_name] = redact_log_value(
                    value, field_name=field_name
                )

        return True


def configure_log_redaction() -> None:
    """Attach the redaction filter to root and Uvicorn loggers once per process."""
    for logger_name in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(existing_filter, ClinicalContentRedactionFilter)
            for existing_filter in logger.filters
        ):
            logger.addFilter(ClinicalContentRedactionFilter())

        for handler in logger.handlers:
            if not any(
                isinstance(existing_filter, ClinicalContentRedactionFilter)
                for existing_filter in handler.filters
            ):
                handler.addFilter(ClinicalContentRedactionFilter())
